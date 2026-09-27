from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .exceptions import (
    AlreadyReturned,
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


def return_checkout(
    *,
    checkout_id: int,
    condition_note: str = "",
    needs_maintenance: bool = False,
    now: datetime | None = None,
) -> CheckOut:
    """Close an open check-out and release (or quarantine) its asset.

    404 NotFound for an unknown id, 409 AlreadyReturned if already closed.
    """
    now = now or timezone.now()
    with transaction.atomic():
        # Lock the checkout first so two concurrent returns can't both pass the
        # returned_at check. select_related is avoided here: FOR UPDATE would then
        # also lock the joined rows, and we want to lock the asset explicitly.
        try:
            checkout = CheckOut.objects.select_for_update().get(pk=checkout_id)
        except CheckOut.DoesNotExist:
            raise NotFound(f"Check-out {checkout_id} not found.")
        if checkout.returned_at is not None:
            raise AlreadyReturned()

        # checkout -> asset order; check-out creation never locks CheckOut rows,
        # so this cannot form a cycle with employee -> asset.
        asset = Asset.objects.select_for_update().get(pk=checkout.asset_id)

        checkout.returned_at = now
        checkout.condition_note = condition_note
        checkout.save(update_fields=["returned_at", "condition_note"])

        asset.status = Asset.Status.MAINTENANCE if needs_maintenance else Asset.Status.AVAILABLE
        asset.save(update_fields=["status", "updated_at"])

    checkout.asset = asset
    return checkout


def create_asset(
    *,
    asset_tag: str,
    name: str,
    category: str,
    purchase_date: date,
    status: str = Asset.Status.AVAILABLE,
) -> Asset:
    try:
        with transaction.atomic():
            return Asset.objects.create(
                asset_tag=asset_tag,
                name=name,
                category=category,
                purchase_date=purchase_date,
                status=status,
            )
    except IntegrityError:
        # Lost a race with a concurrent create of the same tag.
        raise ValidationError({"asset_tag": ["asset with this asset tag already exists."]})
