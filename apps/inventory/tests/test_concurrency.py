import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.inventory.models import Asset, CheckOut

URL = "/api/v1/checkouts/"


def _race(token_key: str, payloads: list[dict]) -> list[int]:
    """POST each payload from its own thread, released together by a barrier."""
    barrier = threading.Barrier(len(payloads))
    statuses: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(payload: dict) -> None:
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")
            barrier.wait(timeout=10)
            resp = client.post(URL, payload, format="json")
            with lock:
                statuses.append(resp.status_code)
        except BaseException as exc:
            with lock:
                errors.append(exc)
        finally:
            # Each thread opens its own DB connection; close it so the test DB can be dropped.
            connection.close()

    threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    return sorted(statuses)


@pytest.fixture
def token_key(transactional_db) -> str:
    user = get_user_model().objects.create_user(username="racer", password="pw")
    return Token.objects.create(user=user).key


def _due() -> str:
    return (timezone.now() + timedelta(days=7)).isoformat()


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_of_same_asset_yields_exactly_one_success(token_key, make_asset, make_employee):
    asset = make_asset()
    first, second = make_employee(), make_employee()

    statuses = _race(
        token_key,
        [
            {"asset_tag": asset.asset_tag, "employee_code": first.employee_code, "due_at": _due()},
            {"asset_tag": asset.asset_tag, "employee_code": second.employee_code, "due_at": _due()},
        ],
    )

    assert statuses == [201, 409]
    assert CheckOut.objects.filter(asset=asset).count() == 1
    asset.refresh_from_db()
    assert asset.status == Asset.Status.CHECKED_OUT


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkouts_by_same_employee_respect_limit(token_key, make_asset, make_employee, make_checkout):
    employee = make_employee()
    make_checkout(asset=make_asset(), employee=employee)
    make_checkout(asset=make_asset(), employee=employee)
    a, b = make_asset(), make_asset()

    statuses = _race(
        token_key,
        [
            {"asset_tag": a.asset_tag, "employee_code": employee.employee_code, "due_at": _due()},
            {"asset_tag": b.asset_tag, "employee_code": employee.employee_code, "due_at": _due()},
        ],
    )

    assert statuses == [201, 409]
    assert CheckOut.objects.filter(employee=employee, returned_at__isnull=True).count() == 3
    a.refresh_from_db()
    b.refresh_from_db()
    assert sorted([a.status, b.status]) == sorted([Asset.Status.AVAILABLE, Asset.Status.CHECKED_OUT])
