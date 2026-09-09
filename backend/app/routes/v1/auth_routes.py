from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.auth_controller import AuthController
from app.core.dependencies import CurrentUser, get_current_user, get_db, require_admin, require_manager_or_admin
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
    VerifyOtpRequest,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register")
async def register(payload: RegisterRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).register(payload)


@router.post("/login")
async def login(payload: LoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).login(payload)


@router.post("/refresh")
async def refresh(payload: RefreshTokenRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).refresh(payload)


@router.post("/logout")
async def logout(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Server-side logout: bumps token_version so EVERY refresh token this
    user holds dies immediately (access tokens age out within 15 min).
    Client-side clearing alone left a stolen refresh token valid for its
    full 7 days. Per-token rotation with reuse detection stays a known
    deferral (needs a jti store); this closes the practical gap."""
    return await AuthController(db).logout(current_user)


@router.get("/me")
async def me(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).me(current_user)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AuthController(db).change_password(current_user, payload)


@router.post("/otp/request")
async def request_otp(payload: RequestOtpRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).request_otp(payload)


@router.post("/otp/verify")
async def verify_otp(payload: VerifyOtpRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).verify_otp(payload)


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).forgot_password(payload)


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await AuthController(db).reset_password(payload)


@router.post("/staff", dependencies=[Depends(require_manager_or_admin)])
async def create_staff(
    payload: StaffCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AuthController(db).create_staff(current_user, payload)


@router.post("/customers", dependencies=[Depends(require_manager_or_admin)])
async def create_customer(
    payload: ManagerCreateCustomerRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Manager/admin creates a customer account on their behalf (e.g. a
    phone/walk-in booking) with a temp password — see
    UserModel.must_change_password."""
    return await AuthController(db).create_customer(current_user, payload)


@router.post("/verify-phone/request")
async def request_phone_verification(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Sends an OTP to the LOGGED-IN customer's own phone — the gate a
    first-time self-service booking/subscription blocks on until
    completed (PhoneNotVerifiedException). Always the caller's own phone;
    never takes a target identifier."""
    return await AuthController(db).request_phone_verification(current_user)


@router.post("/verify-phone/confirm")
async def confirm_phone_verification(
    payload: ConfirmPhoneVerificationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await AuthController(db).confirm_phone_verification(current_user, payload)


@router.post("/customers/{customer_id}/reset-password", dependencies=[Depends(require_manager_or_admin)])
async def staff_reset_customer_password(
    customer_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Manager/admin resets a customer's forgotten password on their
    behalf — the generated temp password is sent straight to the
    customer's WhatsApp and is never included in this response, so it's
    never visible to the manager triggering it."""
    return await AuthController(db).staff_reset_customer_password(current_user, customer_id)

# ---- MSG91 OTP widget (server-verified) -----------------------------------
from pydantic import BaseModel as _BaseModel, Field as _WField  # noqa: E402

from app.services.auth_service import AuthService  # noqa: E402
from app.services.msg91_widget_service import Msg91WidgetService  # noqa: E402
from app.core.responses import success  # noqa: E402


class WidgetTokenRequest(_BaseModel):
    access_token: str = _WField(min_length=10, max_length=4096)


class WidgetResetRequest(_BaseModel):
    access_token: str = _WField(min_length=10, max_length=4096)
    phone: str = _WField(min_length=10, max_length=15)
    new_password: str = _WField(min_length=8, max_length=72)


@router.get("/otp-widget-config")
async def otp_widget_config():
    """Public: tells the frontend whether the MSG91 widget is configured
    and hands it the client-side ids (never the server auth key)."""
    return success(Msg91WidgetService().public_config())


@router.post("/verify-phone/widget")
async def verify_phone_via_widget(
    payload: WidgetTokenRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    updated = await AuthService(db).confirm_phone_verification_widget(current_user.id, payload.access_token)
    return success({"phone_verified": bool(updated.get("phone_verified"))}, message="Phone verified")


@router.post("/reset-password/widget")
async def reset_password_via_widget(payload: WidgetResetRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    await AuthService(db).reset_password_widget(payload.access_token, payload.phone, payload.new_password)
    return success({}, message="Password reset — you can log in now")


class BookingAccessRequest(_BaseModel):
    phone: str = _WField(min_length=10, max_length=15)


class OtpLoginRequest(_BaseModel):
    phone: str = _WField(min_length=10, max_length=15)
    otp: str | None = _WField(default=None, min_length=4, max_length=8)
    access_token: str | None = _WField(default=None, min_length=10, max_length=4096)


class SetPasswordRequest(_BaseModel):
    new_password: str = _WField(min_length=8, max_length=72)


@router.post("/booking-access")
async def booking_access(payload: BookingAccessRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Guest wizard: which credential should this phone be asked for —
    register / otp (unverified or stale account) / password (established)."""
    return success(await AuthService(db).booking_access_mode(payload.phone))


@router.post("/otp-login")
async def otp_login(payload: OtpLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Customer login by phone-ownership proof (classic OTP or MSG91 widget
    token) — the abandoned-signup recovery and 90-day re-verification path."""
    return success(await AuthService(db).otp_login(payload.phone, payload.otp, payload.access_token))


@router.post("/set-password")
async def set_password(
    payload: SetPasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The mandatory first-password gate (must_change_password accounts) —
    no current password needed, identity was already proven."""
    await AuthService(db).set_initial_password(current_user.id, payload.new_password)
    return success({}, message="Password set — you're all set")


# ---- Google sign-in + Maps public config ----------------------------------
from app.services.google_auth_service import GoogleAuthService  # noqa: E402


class GoogleLoginRequest(_BaseModel):
    credential: str = _WField(min_length=20, max_length=8192)


class AddPhoneRequest(_BaseModel):
    phone: str = _WField(min_length=10, max_length=15)


class AddPhoneConfirmRequest(_BaseModel):
    phone: str = _WField(min_length=10, max_length=15)
    otp: str | None = _WField(default=None, min_length=4, max_length=8)
    access_token: str | None = _WField(default=None, min_length=10, max_length=4096)


@router.get("/google-config")
async def google_config():
    return success(GoogleAuthService.public_config())


@router.get("/maps-config")
async def maps_config():
    """Browser Maps key — public by design (protected by key restrictions
    in Google Cloud console, never by secrecy)."""
    from app.core.config import settings as _settings

    return success({
        "enabled": bool(_settings.GOOGLE_MAPS_BROWSER_KEY),
        "browser_key": _settings.GOOGLE_MAPS_BROWSER_KEY or None,
    })


@router.post("/google")
async def google_login(payload: GoogleLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    return success(await GoogleAuthService(db).login_with_google(payload.credential))


@router.post("/add-phone/request")
async def add_phone_request(
    payload: AddPhoneRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """A phoneless account (Google sign-in) attaching its primary contact
    number — sends the OTP to the NEW number."""
    await AuthService(db).add_phone_request(current_user.id, payload.phone)
    return success({}, message="Code sent")


@router.post("/add-phone/confirm")
async def add_phone_confirm(
    payload: AddPhoneConfirmRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    await AuthService(db).add_phone_confirm(current_user.id, payload.phone, payload.otp, payload.access_token)
    return success({}, message="Phone verified and saved")
