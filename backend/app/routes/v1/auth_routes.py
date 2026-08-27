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
