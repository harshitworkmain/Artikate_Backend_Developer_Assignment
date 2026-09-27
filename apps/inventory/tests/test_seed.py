from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.inventory.models import Asset, CheckOut, Employee, OverdueNotice


def _seed() -> str:
    out = StringIO()
    call_command("seed_demo_data", stdout=out)
    return out.getvalue()


def _counts() -> dict:
    return {
        "assets": Asset.objects.count(),
        "employees": Employee.objects.count(),
        "checkouts": CheckOut.objects.count(),
        "open": CheckOut.objects.filter(returned_at__isnull=True).count(),
        "users": get_user_model().objects.count(),
        "tokens": Token.objects.count(),
    }


def _assert_status_invariant() -> None:
    open_asset_ids = set(CheckOut.objects.filter(returned_at__isnull=True).values_list("asset_id", flat=True))
    checked_out_ids = set(Asset.objects.filter(status=Asset.Status.CHECKED_OUT).values_list("id", flat=True))
    assert open_asset_ids == checked_out_ids


@pytest.mark.django_db
def test_seed_is_idempotent():
    _seed()
    first = _counts()
    first_token = Token.objects.get(user__username="demo").key

    output = _seed()

    assert _counts() == first
    assert Token.objects.get(user__username="demo").key == first_token
    assert first_token in output
    _assert_status_invariant()


@pytest.mark.django_db
def test_seed_data_shape():
    _seed()
    now = timezone.now()
    open_cos = CheckOut.objects.filter(returned_at__isnull=True)
    returned = CheckOut.objects.filter(returned_at__isnull=False)

    assert Asset.objects.count() >= 8
    assert set(Asset.objects.values_list("category", flat=True)) == set(Asset.Category.values)
    assert Asset.objects.filter(status=Asset.Status.MAINTENANCE).exists()
    assert Employee.objects.count() >= 4
    assert Employee.objects.filter(is_active=False).count() == 1
    assert open_cos.filter(due_at__lt=now).count() >= 2
    assert open_cos.filter(due_at__gt=now).count() >= 1
    assert sum(1 for c in returned if c.returned_at <= c.due_at) >= 2
    assert sum(1 for c in returned if c.returned_at > c.due_at) >= 1
    assert all(c.checked_out_at < c.due_at for c in CheckOut.objects.all())
    assert not open_cos.filter(employee__is_active=False).exists()
    demo = get_user_model().objects.get(username="demo")
    assert not demo.is_staff and not demo.is_superuser
    _assert_status_invariant()


@pytest.mark.django_db
def test_reseed_restores_state_and_drops_notices(api_client):
    _seed()
    checkout = CheckOut.objects.filter(returned_at__isnull=True).first()
    OverdueNotice.objects.create(checkout=checkout, notice_date=timezone.localdate())
    api_client.post(f"/api/v1/checkouts/{checkout.pk}/return/", {"needs_maintenance": True}, format="json")

    _seed()

    assert not OverdueNotice.objects.exists()
    _assert_status_invariant()


@pytest.mark.django_db
def test_seed_leaves_non_demo_data_alone(make_asset, make_employee, make_checkout):
    other = make_asset(asset_tag="REAL-1")
    make_checkout(asset=other, employee=make_employee())

    _seed()

    assert CheckOut.objects.filter(asset=other, returned_at__isnull=True).exists()
    other.refresh_from_db()
    assert other.status == Asset.Status.CHECKED_OUT
