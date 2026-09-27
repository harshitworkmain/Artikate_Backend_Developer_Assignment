import itertools
from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.inventory.models import Asset, CheckOut, Employee

_seq = itertools.count(1)


@pytest.fixture
def make_asset(db):
    def _make(**kwargs) -> Asset:
        n = next(_seq)
        defaults = {
            "asset_tag": f"TAG-{n:04d}",
            "name": f"Asset {n}",
            "category": Asset.Category.LAPTOP,
            "purchase_date": date(2024, 1, 1),
        }
        defaults.update(kwargs)
        return Asset.objects.create(**defaults)

    return _make


@pytest.fixture
def make_employee(db):
    def _make(**kwargs) -> Employee:
        n = next(_seq)
        defaults = {
            "employee_code": f"EMP-{n:04d}",
            "full_name": f"Employee {n}",
            "email": f"employee{n}@example.com",
        }
        defaults.update(kwargs)
        return Employee.objects.create(**defaults)

    return _make


@pytest.fixture
def make_checkout(db):
    """Create a CheckOut directly via the ORM (bypassing business rules)."""

    def _make(*, asset, employee, due_at=None, returned_at=None, checked_out_at=None) -> CheckOut:
        checkout = CheckOut.objects.create(
            asset=asset,
            employee=employee,
            due_at=due_at or timezone.now() + timedelta(days=7),
            returned_at=returned_at,
        )
        if checked_out_at is not None:
            # auto_now_add ignores values passed to create(), so override afterwards.
            CheckOut.objects.filter(pk=checkout.pk).update(checked_out_at=checked_out_at)
            checkout.refresh_from_db()
        if returned_at is None:
            asset.status = Asset.Status.CHECKED_OUT
            asset.save(update_fields=["status", "updated_at"])
        return checkout

    return _make


@pytest.fixture
def api_client(db) -> APIClient:
    user = get_user_model().objects.create_user(username="tester", password="pw")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client
