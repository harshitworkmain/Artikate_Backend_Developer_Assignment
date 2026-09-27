from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import (
    AssetUnavailable,
    CheckoutLimitReached,
    InactiveEmployee,
    InvalidDueDate,
    NotFound,
)
from .models import Asset, CheckOut, Employee

MAX_OPEN_CHECKOUTS = 3
MAX_CHECKOUT_DURATION = timedelta(days=30)
OPEN_CHECKOUT_CONSTRAINT = "uniq_open_checkout_per_asset"


def _is_constraint_violation(exc: IntegrityError, name: str) -> bool:
    diag = getattr(exc.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None) == name


def checkout_asset(
    *,
    asset_tag: str,
    employee_code: str,
    due_at: datetime,
    now: datetime | None = None,
) -> CheckOut:
    """Check an asset out to an employee.

    Error precedence (first failing check wins):
      400 InvalidDueDate       -> due_at not in (now, now + 30 days]
      404 NotFound             -> unknown employee_code
      400 InactiveEmployee     -> employee.is_active is False
      404 NotFound             -> unknown asset_tag
      409 AssetUnavailable     -> asset.status != AVAILABLE
      409 CheckoutLimitReached -> employee already holds 3 open check-outs
    """
    now = now or timezone.now()
    if not (now < due_at <= now + MAX_CHECKOUT_DURATION):
        raise InvalidDueDate()

    with transaction.atomic():
        # Locking the employee serialises concurrent check-outs by the same
        # employee, so the open-check-out limit cannot be raced.
        try:
            employee = Employee.objects.select_for_update().get(employee_code=employee_code)
        except Employee.DoesNotExist:
            raise NotFound(f"Employee '{employee_code}' not found.")
        if not employee.is_active:
            raise InactiveEmployee()

        # Locks are always taken employee -> asset; a consistent order means
        # two transactions can never wait on each other (no deadlocks).
        try:
            asset = Asset.objects.select_for_update().get(asset_tag=asset_tag)
        except Asset.DoesNotExist:
            raise NotFound(f"Asset '{asset_tag}' not found.")
        if asset.status != Asset.Status.AVAILABLE:
            raise AssetUnavailable()

        open_count = CheckOut.objects.filter(employee=employee, returned_at__isnull=True).count()
        if open_count >= MAX_OPEN_CHECKOUTS:
            raise CheckoutLimitReached()

        # The partial unique constraint is the DB-level backstop; the savepoint
        # keeps the outer transaction usable if it fires.
        try:
            with transaction.atomic():
                checkout = CheckOut.objects.create(asset=asset, employee=employee, due_at=due_at)
        except IntegrityError as exc:
            if _is_constraint_violation(exc, OPEN_CHECKOUT_CONSTRAINT):
                raise AssetUnavailable()
            raise

        asset.status = Asset.Status.CHECKED_OUT
        asset.save(update_fields=["status", "updated_at"])

    return checkout
