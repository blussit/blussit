"""
Domain-level exceptions, mapped to consistent HTTP responses by the
global exception handlers registered in app.main.
"""


class AppException(Exception):
    status_code: int = 500
    error_code: str = "SERVER_ERROR"

    def __init__(self, message: str = "Something went wrong", details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class NotFoundException(AppException):
    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, message: str = "Resource not found", details: dict | None = None):
        super().__init__(message, details)


class ValidationException(AppException):
    status_code = 422
    error_code = "VALIDATION_ERROR"

    def __init__(self, message: str = "Validation failed", details: dict | None = None):
        super().__init__(message, details)


class UnauthorizedException(AppException):
    status_code = 401
    error_code = "UNAUTHORIZED"

    def __init__(self, message: str = "Authentication required", details: dict | None = None):
        super().__init__(message, details)


class ForbiddenException(AppException):
    status_code = 403
    error_code = "FORBIDDEN"

    def __init__(self, message: str = "You do not have permission to perform this action", details: dict | None = None):
        super().__init__(message, details)


class ConflictException(AppException):
    status_code = 409
    error_code = "CONFLICT"

    def __init__(self, message: str = "Resource conflict", details: dict | None = None):
        super().__init__(message, details)


class BadRequestException(AppException):
    status_code = 400
    error_code = "BAD_REQUEST"

    def __init__(self, message: str = "Bad request", details: dict | None = None):
        super().__init__(message, details)


class PhoneNotVerifiedException(ForbiddenException):
    """Raised when a customer tries to book/subscribe for themselves
    without ever having completed OTP phone verification. Deliberately a
    distinct error_code (not just a ForbiddenException with a message) so
    the frontend can detect this exact case and open the OTP modal instead
    of showing a generic error banner — see PhoneVerificationModal.tsx."""
    error_code = "PHONE_NOT_VERIFIED"

    def __init__(self, message: str = "Please verify your phone number with an OTP before booking.", details: dict | None = None):
        super().__init__(message, details)
