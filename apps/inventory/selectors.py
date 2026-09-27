from datetime import datetime

from django.db import connection
from django.db.models import (
    Avg,
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
)
from django.db.models.functions import ExtractDay
from django.utils import timezone

from .exceptions import NotFound
from .models import Asset, CheckOut, Employee

SECONDS_PER_DAY = 86400


def asset_queryset(*, now: datetime | None = None) -> QuerySet[Asset]:
    """Assets annotated with their current holder (holder_code / holder_name).

    The holder comes from the open CheckOut via correlated subqueries, so a
    list page is a single query regardless of size. `now` is accepted for
    selector-signature consistency; "current" is defined by returned_at IS NULL.
    """
    open_checkout = CheckOut.objects.filter(asset=OuterRef("pk"), returned_at__isnull=True)
    return Asset.objects.annotate(
        holder_code=Subquery(open_checkout.values("employee__employee_code")[:1]),
        holder_name=Subquery(open_checkout.values("employee__full_name")[:1]),
    )


def employee_summary(*, employee_code: str, now: datetime | None = None) -> dict:
    """Lifetime and current check-out stats for one employee, in a single query.

    Overdue means open and due_at < now. mean_hold_days averages
    returned_at - checked_out_at over returned check-outs only; it is None
    when the employee has never returned anything.
    """
    now = now or timezone.now()
    is_open = Q(checkouts__returned_at__isnull=True)
    try:
        row = (
            Employee.objects.filter(employee_code=employee_code)
            .annotate(
                lifetime_checkouts=Count("checkouts"),
                currently_held=Count("checkouts", filter=is_open),
                currently_overdue=Count("checkouts", filter=is_open & Q(checkouts__due_at__lt=now)),
                mean_hold=Avg(
                    ExpressionWrapper(
                        F("checkouts__returned_at") - F("checkouts__checked_out_at"),
                        output_field=DurationField(),
                    ),
                    filter=Q(checkouts__returned_at__isnull=False),
                ),
            )
            .values(
                "employee_code",
                "full_name",
                "is_active",
                "lifetime_checkouts",
                "currently_held",
                "currently_overdue",
                "mean_hold",
            )
            .get()
        )
    except Employee.DoesNotExist:
        raise NotFound(f"Employee '{employee_code}' not found.")

    mean_hold = row.pop("mean_hold")
    row["mean_hold_days"] = (
        round(mean_hold.total_seconds() / SECONDS_PER_DAY, 2) if mean_hold is not None else None
    )
    return row


def overdue_checkouts(*, now: datetime | None = None) -> QuerySet[CheckOut]:
    """Open check-outs whose due_at is strictly before `now`, most overdue first.

    An item due exactly at `now` is not yet overdue. days_overdue is computed in
    the database as the whole number of days elapsed since due_at (floored), so
    something 1 hour late reports 0.
    """
    now = now or timezone.now()
    overdue_by = ExpressionWrapper(
        Value(now, output_field=DateTimeField()) - F("due_at"),
        output_field=DurationField(),
    )
    return (
        CheckOut.objects.filter(returned_at__isnull=True, due_at__lt=now)
        .select_related("asset", "employee")
        .annotate(days_overdue=ExtractDay(overdue_by))
        .order_by("due_at", "id")
    )


def database_is_healthy() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return False
    return True
