from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """DRF's handler, plus a machine-readable "code" next to "detail"."""
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "detail" in response.data:
        response.data["code"] = getattr(response.data["detail"], "code", None)
    return response


class NotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Resource not found."
    default_code = "not_found"


class InactiveEmployee(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Employee is inactive."
    default_code = "inactive_employee"


class InvalidDueDate(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "due_at must be in the future and at most 30 days from now."
    default_code = "invalid_due_date"


class AssetUnavailable(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Asset is not available for check-out."
    default_code = "asset_unavailable"


class CheckoutLimitReached(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Employee already holds the maximum of 3 open check-outs."
    default_code = "checkout_limit_reached"


class AlreadyReturned(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Check-out has already been returned."
    default_code = "already_returned"
