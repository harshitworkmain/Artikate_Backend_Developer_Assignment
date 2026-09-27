from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inventory import selectors
from apps.inventory.exceptions import NotFound


def _url(code: str) -> str:
    return f"/api/v1/employees/{code}/summary/"


@pytest.fixture
def busy_employee(make_asset, make_employee, make_checkout):
    """Employee with 2 returns (2d and 3.5d holds), 1 open overdue and 1 open not yet due."""
    now = timezone.now()
    employee = make_employee(full_name="Grace Hopper")
    make_checkout(
        asset=make_asset(), employee=employee,
        checked_out_at=now - timedelta(days=10), due_at=now - timedelta(days=7),
        returned_at=now - timedelta(days=8),
    )
    make_checkout(
        asset=make_asset(), employee=employee,
        checked_out_at=now - timedelta(days=6), due_at=now - timedelta(days=1),
        returned_at=now - timedelta(days=2, hours=12),
    )
    make_checkout(asset=make_asset(), employee=employee, due_at=now - timedelta(hours=1))
    make_checkout(asset=make_asset(), employee=employee, due_at=now + timedelta(days=2))
    # Another employee's history must not leak into the numbers.
    make_checkout(asset=make_asset(), employee=make_employee(), due_at=now - timedelta(days=3))
    return employee, now


@pytest.mark.django_db
class TestEmployeeSummarySelector:
    def test_numbers(self, busy_employee):
        employee, now = busy_employee

        summary = selectors.employee_summary(employee_code=employee.employee_code, now=now)

        assert summary == {
            "employee_code": employee.employee_code,
            "full_name": "Grace Hopper",
            "is_active": True,
            "lifetime_checkouts": 4,
            "currently_held": 2,
            "currently_overdue": 1,
            "mean_hold_days": 2.75,
        }

    def test_single_query(self, busy_employee, django_assert_num_queries):
        employee, now = busy_employee

        with django_assert_num_queries(1):
            selectors.employee_summary(employee_code=employee.employee_code, now=now)

    def test_mean_is_null_without_returns(self, make_asset, make_employee, make_checkout):
        employee = make_employee()
        make_checkout(asset=make_asset(), employee=employee)

        summary = selectors.employee_summary(employee_code=employee.employee_code)

        assert summary["lifetime_checkouts"] == 1
        assert summary["currently_held"] == 1
        assert summary["mean_hold_days"] is None

    def test_employee_without_history(self, make_employee):
        employee = make_employee()

        summary = selectors.employee_summary(employee_code=employee.employee_code)

        assert summary["lifetime_checkouts"] == 0
        assert summary["currently_held"] == 0
        assert summary["currently_overdue"] == 0
        assert summary["mean_hold_days"] is None

    def test_due_exactly_now_is_not_overdue(self, make_asset, make_employee, make_checkout):
        employee = make_employee()
        now = timezone.now()
        make_checkout(asset=make_asset(), employee=employee, due_at=now)

        summary = selectors.employee_summary(employee_code=employee.employee_code, now=now)

        assert summary["currently_overdue"] == 0

    def test_unknown_employee(self):
        with pytest.raises(NotFound):
            selectors.employee_summary(employee_code="NOPE")


@pytest.mark.django_db
class TestEmployeeSummaryEndpoint:
    def test_ok(self, api_client, busy_employee):
        employee, _ = busy_employee

        resp = api_client.get(_url(employee.employee_code))

        assert resp.status_code == 200
        body = resp.json()
        assert body["lifetime_checkouts"] == 4
        assert body["currently_held"] == 2
        assert body["currently_overdue"] == 1
        assert body["mean_hold_days"] == 2.75

    def test_unknown_is_404(self, api_client):
        resp = api_client.get(_url("NOPE"))

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
