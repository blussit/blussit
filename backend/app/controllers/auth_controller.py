from motor.motor_asyncio import AsyncIOMotorDatabase

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

    async def me(self, current_user: CurrentUser):
        user = await self.user_service.get_by_id(current_user.id)
        return success(user, "Current user fetched")

    async def change_password(self, current_user: CurrentUser, payload: ChangePasswordRequest):
        await self.auth_service.change_password(current_user.id, payload.current_password, payload.new_password)
        return success(None, "Password changed successfully")

    async def request_otp(self, payload: RequestOtpRequest):
        # SECURITY: the OTP itself is never included in this response —
        # it goes out over WhatsApp only (see AuthService.request_otp /
        # WhatsAppService). Earlier Phase 1 code echoed it back as
        # "debug_otp" for testing before any real delivery existed, which
        # made the whole verification step trivially bypassable (anyone
        # could read the code straight out of the network response).
        await self.auth_service.request_otp(payload.identifier)
        return success({"otp_sent": True}, "Verification code sent")

    async def verify_otp(self, payload: VerifyOtpRequest):
        verified = await self.auth_service.verify_otp(payload.identifier, payload.otp)
        return success({"verified": verified}, "OTP verified" if verified else "OTP verification failed")

    async def forgot_password(self, payload: ForgotPasswordRequest):
        await self.auth_service.request_otp(payload.identifier, purpose="password_reset")
        return success({"otp_sent": True}, "A reset code has been sent to your WhatsApp")

    async def reset_password(self, payload: ResetPasswordRequest):
        await self.auth_service.reset_password(payload.identifier, payload.otp, payload.new_password)
        return success(None, "Password reset successfully")

    async def request_phone_verification(self, current_user: CurrentUser):
        await self.auth_service.request_phone_verification(current_user.id)
        return success({"otp_sent": True}, "A verification code has been sent to your WhatsApp")

    async def confirm_phone_verification(self, current_user: CurrentUser, payload: ConfirmPhoneVerificationRequest):
        user = await self.auth_service.confirm_phone_verification(current_user.id, payload.otp)
        return success(UserPublic.from_doc(user).model_dump(), "Phone verified")

    async def staff_reset_customer_password(self, current_user: CurrentUser, customer_id: str):
        await self.auth_service.staff_reset_customer_password(customer_id, current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "RESET_CUSTOMER_PASSWORD", "users", customer_id, {})
        # Deliberately no password in this response — see
        # AuthService.staff_reset_customer_password's docstring.
        return success(None, "A new temporary password has been sent to the customer's WhatsApp")

    async def create_staff(self, current_user: CurrentUser, payload: StaffCreateRequest):
        if current_user.role == "manager":
            manager = await self.user_service.get_by_id(current_user.id)
            payload.service_center_id = manager.get("service_center_id")
        result = await self.auth_service.create_staff_account(payload, created_by=current_user.id, creator_role=current_user.role)
        return success(result, "Staff account created successfully")

    async def create_customer(self, current_user: CurrentUser, payload: ManagerCreateCustomerRequest):
        result = await self.auth_service.create_customer_by_staff(payload, created_by=current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "MANAGER_CREATE_CUSTOMER", "users", result["id"], {"phone": payload.phone})
        return success(result, "Customer account created successfully")
