from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.authz import ensure_customer_in_scope, manager_center_or_raise
from app.core.dependencies import CurrentUser
from app.core.responses import success
from app.schemas.user_schema import (
    ChangePasswordRequest,
    ConfirmPhoneVerificationRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ManagerCreateCustomerRequest,
    RefreshTokenRequest,
    RegisterRequest,
    RequestOtpRequest,
    ResetPasswordRequest,
    StaffCreateRequest,
    UserPublic,
    VerifyOtpRequest,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.user_service import UserService


class AuthController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.auth_service = AuthService(db)
        self.user_service = UserService(db)
        self.audit = AuditService(db)

    async def register(self, payload: RegisterRequest):
        result = await self.auth_service.register_customer(payload)
        return success(result, "Account created successfully")

    async def login(self, payload: LoginRequest):
        result = await self.auth_service.login(payload.identifier, payload.password)
        return success(result, "Login successful")

    async def refresh(self, payload: RefreshTokenRequest):
        result = await self.auth_service.refresh(payload.refresh_token)
        return success(result, "Token refreshed")

    async def logout(self, current_user: CurrentUser):
        await self.auth_service.logout(current_user.id)
        return success(None, "Logged out")

    async def me(self, current_user: CurrentUser):
        user = await self.user_service.get_by_id(current_user.id)
        return success(user, "Current user fetched")

    async def change_password(self, current_user: CurrentUser, payload: ChangePasswordRequest):
        await self.auth_service.change_password(current_user.id, payload.current_password, payload.new_password)
        # The change bumps token_version, signing out every OTHER device —
        # this one gets a fresh pair minted at the new version so the person
        # who just changed it isn't bounced to the login page too.
        user = await self.auth_service.users.find_by_id(current_user.id)
        tokens = self.auth_service._issue_tokens(user)
        return success(
            {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "token_type": "bearer"},
            "Password changed — other devices have been signed out",
        )

    async def request_otp(self, payload: RequestOtpRequest):
        # SECURITY: the OTP itself is never included in this response.
        # Earlier Phase 1 code echoed it back as "debug_otp" for testing
        # before any real delivery existed, which made the whole
        # verification step trivially bypassable.
        known = await self.auth_service._find_user_by_identifier(payload.identifier)
        channel = await self.auth_service.request_otp(payload.identifier, customer_only=True)
        return success({"otp_sent": True, "channel": channel, "new_account": known is None}, "Verification code sent")

    async def verify_otp(self, payload: VerifyOtpRequest):
        verified = await self.auth_service.verify_otp(payload.identifier, payload.otp)
        return success({"verified": verified}, "OTP verified" if verified else "OTP verification failed")

    async def forgot_password(self, payload: ForgotPasswordRequest):
        channel = await self.auth_service.request_otp(payload.identifier, purpose="password_reset")
        return success({"otp_sent": True, "channel": channel}, "Reset code sent")

    async def reset_password(self, payload: ResetPasswordRequest):
        await self.auth_service.reset_password(payload.identifier, payload.otp, payload.new_password)
        return success(None, "Password reset successfully")

    async def request_phone_verification(self, current_user: CurrentUser):
        channel = await self.auth_service.request_phone_verification(current_user.id)
        return success({"otp_sent": True, "channel": channel}, "Verification code sent")

    async def confirm_phone_verification(self, current_user: CurrentUser, payload: ConfirmPhoneVerificationRequest):
        user = await self.auth_service.confirm_phone_verification(current_user.id, payload.otp)
        return success(UserPublic.from_doc(user).model_dump(), "Phone verified")

    async def staff_reset_customer_password(self, current_user: CurrentUser, customer_id: str):
        # A manager resets passwords only for customers their center has
        # served — otherwise any manager could sign every customer on the
        # platform out (and spam their WhatsApp) by id.
        await ensure_customer_in_scope(self.db, current_user.role, current_user.service_center_id, customer_id)
        await self.auth_service.staff_reset_customer_password(customer_id, current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "RESET_CUSTOMER_PASSWORD", "users", customer_id, {})
        # Deliberately no password in this response — see
        # AuthService.staff_reset_customer_password's docstring.
        return success(None, "A new temporary password has been sent to the customer's WhatsApp")

    async def create_staff(self, current_user: CurrentUser, payload: StaffCreateRequest):
        if current_user.role == "manager":
            # Always the manager's own center — never the payload's — and a
            # manager with no center linked creates nobody (fail closed).
            payload.service_center_id = manager_center_or_raise(current_user.role, current_user.service_center_id)
        result = await self.auth_service.create_staff_account(payload, created_by=current_user.id, creator_role=current_user.role)
        # ADM-02: every staff account (a new admin included) leaves a trail —
        # who created which role, for which center.
        await self.audit.log_action(
            current_user.id, current_user.role, "CREATE_STAFF", "users", result["id"],
            {"role": payload.role.value, "service_center_id": payload.service_center_id, "email": payload.email, "phone": payload.phone},
            service_center_id=payload.service_center_id,
        )
        return success(result, "Staff account created successfully")

    async def staff_reset_staff_password(self, current_user: CurrentUser, user_id: str, temp_password: str):
        """The staff recovery path — see AuthService.staff_reset_staff_password.
        The temporary password is the one the admin/manager typed; it is not
        echoed back."""
        target = await self.auth_service.staff_reset_staff_password(
            user_id, temp_password, current_user.id, current_user.role, current_user.service_center_id,
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "RESET_STAFF_PASSWORD", "users", user_id,
            {"role": target.get("role")}, service_center_id=target.get("service_center_id"),
        )
        return success(None, "Temporary password set — share it privately; they'll choose their own at the next login")

    async def create_customer(self, current_user: CurrentUser, payload: ManagerCreateCustomerRequest):
        result = await self.auth_service.create_customer_by_staff(payload, created_by=current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "MANAGER_CREATE_CUSTOMER", "users", result["id"], {"phone": payload.phone})
        return success(result, "Customer account created successfully")
