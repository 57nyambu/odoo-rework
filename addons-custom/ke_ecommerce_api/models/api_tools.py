"""Helpers shared by the e-commerce API models and controller."""
from datetime import datetime, timedelta, timezone

API_VERSION = "1.0"
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
EAT = timezone(timedelta(hours=3), "EAT")


class ApiError(Exception):
    """Error returned to the API client as JSON.

    :param status: HTTP status code
    :param code: stable machine readable code (documented in docs/)
    :param message: human readable explanation
    :param details: optional dict / list with field level information
    """

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def parse_datetime(value, field="datetime"):
    """Parse an ISO 8601 timestamp into a naive UTC datetime (Odoo storage
    format). Naive inputs are taken as East Africa Time (UTC+3)."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as error:
            raise ApiError(400, "invalid_parameter", f"{field} must be an ISO 8601 date-time.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EAT)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def format_datetime(value):
    """Naive UTC datetime -> ISO 8601 string with explicit UTC offset."""
    if not value:
        return None
    return value.replace(microsecond=0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def format_date(value):
    return value.isoformat() if value else None


def as_float(value, field, *, minimum=None, required=False):
    if value in (None, ""):
        if required:
            raise ApiError(422, "validation_error", f"{field} is required.", {field: "required"})
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ApiError(422, "validation_error", f"{field} must be a number.", {field: "not a number"}) from error
    if minimum is not None and number < minimum:
        raise ApiError(422, "validation_error", f"{field} must be at least {minimum}.", {field: f">= {minimum}"})
    return number


def page_params(params):
    try:
        page = max(int(params.get("page") or 1), 1)
        size = int(params.get("page_size") or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError) as error:
        raise ApiError(400, "invalid_parameter", "page and page_size must be integers.") from error
    size = min(max(size, 1), MAX_PAGE_SIZE)
    return page, size


def page_meta(page, size, total):
    return {
        "page": page,
        "page_size": size,
        "total": total,
        "has_more": page * size < total,
    }
