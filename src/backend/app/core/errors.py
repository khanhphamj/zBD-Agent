from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(HTTPException):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        field: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.field = field
        self.extra = extra or {}
        super().__init__(status_code=status_code, detail=message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }
        error.update(self.extra)
        return {"error": error}


class AuthDomainError(AppError):
    def __init__(self, domains: list[str]) -> None:
        pretty = " hoặc ".join(f"@{d}" for d in domains)
        super().__init__(
            code="AUTH_DOMAIN",
            message=f"Email này không thuộc {pretty}. Vui lòng dùng email công ty.",
            status_code=status.HTTP_403_FORBIDDEN,
            field="email",
        )


class AuthUnknownError(AppError):
    def __init__(self, email: str) -> None:
        super().__init__(
            code="AUTH_UNKNOWN",
            message=f"{email} chưa có trong hệ thống ZB. Gửi yêu cầu cấp quyền tới Admin?",
            status_code=status.HTTP_404_NOT_FOUND,
            field="email",
        )


class AuthServiceError(AppError):
    def __init__(self, message: str = "Dịch vụ xác thực đang không phản hồi") -> None:
        super().__init__(
            code="AUTH_503",
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class OtpInvalidError(AppError):
    def __init__(self, attempts_left: int) -> None:
        super().__init__(
            code="OTP_INVALID",
            message="Mã xác thực không đúng.",
            status_code=status.HTTP_400_BAD_REQUEST,
            field="code",
            extra={"attemptsLeft": attempts_left},
        )


class OtpExpiredError(AppError):
    def __init__(self) -> None:
        super().__init__(
            code="OTP_EXPIRED",
            message="Mã xác thực đã hết hạn. Vui lòng gửi lại mã.",
            status_code=status.HTTP_410_GONE,
            field="code",
        )


class OtpLockedError(AppError):
    def __init__(self, locked_until: str, minutes: int) -> None:
        super().__init__(
            code="OTP_LOCKED",
            message=f"Bạn đã nhập sai quá nhiều lần. Vui lòng thử lại sau {minutes} phút.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            extra={"lockedUntil": locked_until},
        )


class OtpResendTooSoonError(AppError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            code="OTP_RESEND_TOO_SOON",
            message=f"Vui lòng đợi {retry_after}s trước khi gửi lại mã.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            extra={"retryAfterSeconds": retry_after},
        )


class ServiceUnavailableError(AppError):
    def __init__(
        self, message: str = "Mất kết nối tới dịch vụ ZB", degraded: list[str] | None = None
    ) -> None:
        super().__init__(
            code="SERVICE_503",
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            extra={"degraded": degraded or []},
        )


class NotFoundError(AppError):
    def __init__(self, what: str = "Tài nguyên") -> None:
        super().__init__(
            code="NOT_FOUND",
            message=f"{what} không tồn tại hoặc đã bị xoá.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ForbiddenError(AppError):
    def __init__(self, message: str = "Bạn không có quyền thực hiện thao tác này.") -> None:
        super().__init__(
            code="FORBIDDEN",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Phiên đăng nhập đã hết hạn.") -> None:
        super().__init__(
            code="UNAUTHORIZED",
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ValidationError(AppError):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(
            code="VALIDATION",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            field=field,
        )


class ConflictError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="CONFLICT",
            message=message,
            status_code=status.HTTP_409_CONFLICT,
        )


class FileTypeError(AppError):
    def __init__(self, allowed: list[str], filename: str | None = None) -> None:
        pretty = ", ".join(e.upper() for e in allowed)
        super().__init__(
            code="FILE_TYPE",
            message=f"Định dạng không được hỗ trợ. Chỉ nhận {pretty}.",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            field="file",
            extra={"allowed": allowed, "filename": filename},
        )


class FileSizeError(AppError):
    def __init__(self, max_mb: int, filename: str | None = None) -> None:
        super().__init__(
            code="FILE_SIZE",
            message=f"File vượt giới hạn {max_mb} MB.",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            field="file",
            extra={"maxMb": max_mb, "filename": filename},
        )


class StorageError(AppError):
    def __init__(self, code: str = "STORAGE_500", message: str | None = None) -> None:
        default = (
            "Không thể lưu file lên hệ thống lưu trữ."
            if code == "STORAGE_500"
            else "Mất kết nối tới hệ thống lưu trữ."
        )
        super().__init__(
            code=code,
            message=message or default,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE
            if code == "STORAGE_503"
            else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class ExportError(AppError):
    def __init__(
        self,
        code: str = "EXPORT_422",
        message: str | None = None,
        stage: str | None = None,
        slide_index: int | None = None,
    ) -> None:
        default = (
            "Không thể kết xuất file với nội dung hiện tại."
            if code == "EXPORT_422"
            else "Mất kết nối tới dịch vụ kết xuất."
        )
        super().__init__(
            code=code,
            message=message or default,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
            if code == "EXPORT_422"
            else status.HTTP_503_SERVICE_UNAVAILABLE,
            extra={"stage": stage, "slideIndex": slide_index},
        )


class ResearchError(AppError):
    def __init__(
        self,
        code: str = "RESEARCH_424",
        message: str | None = None,
        stage: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        reasons: list[str] | None = None,
    ) -> None:
        default = (
            "Không đủ nguồn công khai để dựng hồ sơ"
            if code == "RESEARCH_424"
            else "Mất kết nối tới dịch vụ nghiên cứu"
        )
        super().__init__(
            code=code,
            message=message or default,
            status_code=status.HTTP_424_FAILED_DEPENDENCY
            if code == "RESEARCH_424"
            else status.HTTP_503_SERVICE_UNAVAILABLE,
            extra={
                "stage": stage,
                "sources": sources or [],
                "reasons": reasons or [],
            },
        )


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    code = {
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        429: "RATE_LIMITED",
        503: "SERVICE_503",
    }.get(exc.status_code, f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail), "field": None}},
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query", "path")]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION",
                "message": first.get("msg", "Dữ liệu không hợp lệ"),
                "field": ".".join(loc) or None,
                "details": [
                    {
                        "field": ".".join(
                            str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")
                        ),
                        "message": e.get("msg", ""),
                    }
                    for e in exc.errors()
                ],
            }
        },
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    from app.core.logging import get_logger

    get_logger(__name__).exception("unhandled_error", error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL",
                "message": "Đã có lỗi xảy ra. Vui lòng thử lại.",
                "field": None,
            }
        },
    )
