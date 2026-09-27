from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inventory import services
from apps.inventory.exceptions import (
    AssetUnavailable,
    CheckoutLimitReached,
    InactiveEmployee,
    InvalidDueDate,
    NotFound,
)
from apps.inventory.models import Asset, CheckOut

URL = "/api/v1/checkouts/"


def _payload(asset, employee, due_at=None):
    due_at = due_at or timezone.now() + timedelta(days=7)
    return {"asset_tag": asset.asset_tag, "employee_code": employee.employee_code, "due_at": due_at.isoformat()}


@pytest.mark.django_db
class TestCheckoutEndpoint:
    def test_happy_path_creates_checkout_and_marks_asset(self, api_client, make_asset, make_employee):
        asset, employee = make_asset(), make_employee()

        resp = api_client.post(URL, _payload(asset, employee), format="json")

        assert resp.status_code == 201
        body = resp.json()
        assert body["asset_tag"] == asset.asset_tag
        assert body["asset_name"] == asset.name
        assert body["employee_code"] == employee.employee_code
        assert body["employee_name"] == employee.full_name
        assert body["returned_at"] is None
        assert set(body) == {
            "id", "asset_tag", "asset_name", "employee_code", "employee_name",
            "checked_out_at", "due_at", "returned_at", "condition_note",
        }
        assert CheckOut.objects.filter(pk=body["id"], asset=asset, employee=employee).exists()
        asset.refresh_from_db()
        assert asset.status == Asset.Status.CHECKED_OUT

    def test_unavailable_asset_is_409(self, api_client, make_asset, make_employee):
        asset = make_asset(status=Asset.Status.MAINTENANCE)

        resp = api_client.post(URL, _payload(asset, make_employee()), format="json")

        assert resp.status_code == 409
        assert resp.json()["code"] == "asset_unavailable"

    def test_already_checked_out_asset_is_409(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        make_checkout(asset=asset, employee=make_employee())

        resp = api_client.post(URL, _payload(asset, make_employee()), format="json")

        assert resp.status_code == 409
        assert CheckOut.objects.filter(asset=asset).count() == 1

    def test_inactive_employee_is_400(self, api_client, make_asset, make_employee):
        resp = api_client.post(URL, _payload(make_asset(), make_employee(is_active=False)), format="json")

        assert resp.status_code == 400
        assert resp.json() == {"detail": "Employee is inactive.", "code": "inactive_employee"}

    def test_fourth_open_checkout_is_409(self, api_client, make_asset, make_employee):
        employee = make_employee()
        for _ in range(3):
            assert api_client.post(URL, _payload(make_asset(), employee), format="json").status_code == 201
        fourth = make_asset()

        resp = api_client.post(URL, _payload(fourth, employee), format="json")

        assert resp.status_code == 409
        assert resp.json()["code"] == "checkout_limit_reached"
        fourth.refresh_from_db()
        assert fourth.status == Asset.Status.AVAILABLE

    def test_past_due_at_is_400(self, api_client, make_asset, make_employee):
        past = timezone.now() - timedelta(hours=1)

        resp = api_client.post(URL, _payload(make_asset(), make_employee(), due_at=past), format="json")

        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_due_date"

    def test_unknown_asset_tag_is_404(self, api_client, make_employee):
        resp = api_client.post(
            URL,
            {"asset_tag": "NOPE", "employee_code": make_employee().employee_code,
             "due_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_unknown_employee_code_is_404(self, api_client, make_asset):
        resp = api_client.post(
            URL,
            {"asset_tag": make_asset().asset_tag, "employee_code": "NOPE",
             "due_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    @pytest.mark.parametrize("missing", ["asset_tag", "employee_code", "due_at"])
    def test_missing_field_is_400(self, api_client, make_asset, make_employee, missing):
        payload = _payload(make_asset(), make_employee())
        del payload[missing]

        resp = api_client.post(URL, payload, format="json")

        assert resp.status_code == 400
        assert missing in resp.json()

    def test_malformed_due_at_is_400(self, api_client, make_asset, make_employee):
        payload = _payload(make_asset(), make_employee())
        payload["due_at"] = "not-a-date"

        resp = api_client.post(URL, payload, format="json")

        assert resp.status_code == 400
        assert "due_at" in resp.json()


@pytest.mark.django_db
class TestCheckoutService:
    @pytest.mark.parametrize(
        "offset",
        [timedelta(hours=-1), timedelta(0), timedelta(days=30, seconds=1)],
        ids=["past", "exactly-now", "over-30-days"],
    )
    def test_invalid_due_at(self, make_asset, make_employee, offset):
        now = timezone.now()
        with pytest.raises(InvalidDueDate):
            services.checkout_asset(
                asset_tag=make_asset().asset_tag,
                employee_code=make_employee().employee_code,
                due_at=now + offset,
                now=now,
            )
        assert CheckOut.objects.count() == 0

    def test_exactly_30_days_is_allowed(self, make_asset, make_employee):
        now = timezone.now()

        checkout = services.checkout_asset(
            asset_tag=make_asset().asset_tag,
            employee_code=make_employee().employee_code,
            due_at=now + timedelta(days=30),
            now=now,
        )

        assert checkout.pk is not None

    def test_precedence_invalid_due_date_before_not_found(self):
        now = timezone.now()
        with pytest.raises(InvalidDueDate):
            services.checkout_asset(asset_tag="NOPE", employee_code="NOPE", due_at=now, now=now)

    def test_precedence_inactive_before_unknown_asset(self, make_employee):
        with pytest.raises(InactiveEmployee):
            services.checkout_asset(
                asset_tag="NOPE",
                employee_code=make_employee(is_active=False).employee_code,
                due_at=timezone.now() + timedelta(days=1),
            )

    def test_precedence_unavailable_before_limit(self, make_asset, make_employee, make_checkout):
        employee = make_employee()
        for _ in range(3):
            make_checkout(asset=make_asset(), employee=employee)
        with pytest.raises(AssetUnavailable):
            services.checkout_asset(
                asset_tag=make_asset(status=Asset.Status.MAINTENANCE).asset_tag,
                employee_code=employee.employee_code,
                due_at=timezone.now() + timedelta(days=1),
            )

    def test_limit_counts_only_open_checkouts(self, make_asset, make_employee, make_checkout):
        employee = make_employee()
        for _ in range(2):
            make_checkout(asset=make_asset(), employee=employee)
        make_checkout(asset=make_asset(), employee=employee, returned_at=timezone.now())

        checkout = services.checkout_asset(
            asset_tag=make_asset().asset_tag,
            employee_code=employee.employee_code,
            due_at=timezone.now() + timedelta(days=1),
        )

        assert checkout.pk is not None
        with pytest.raises(CheckoutLimitReached):
            services.checkout_asset(
                asset_tag=make_asset().asset_tag,
                employee_code=employee.employee_code,
                due_at=timezone.now() + timedelta(days=1),
            )

    def test_unknown_employee_raises_not_found(self, make_asset):
        with pytest.raises(NotFound):
            services.checkout_asset(
                asset_tag=make_asset().asset_tag,
                employee_code="NOPE",
                due_at=timezone.now() + timedelta(days=1),
            )

    def test_db_constraint_backstop_maps_to_asset_unavailable(self, make_asset, make_employee, make_checkout):
        # Simulate a stale status: an open checkout exists but the asset still says AVAILABLE.
        asset = make_asset()
        make_checkout(asset=asset, employee=make_employee())
        Asset.objects.filter(pk=asset.pk).update(status=Asset.Status.AVAILABLE)

        with pytest.raises(AssetUnavailable):
            services.checkout_asset(
                asset_tag=asset.asset_tag,
                employee_code=make_employee().employee_code,
                due_at=timezone.now() + timedelta(days=1),
            )
        assert CheckOut.objects.filter(asset=asset).count() == 1

    def test_failure_after_create_rolls_back_everything(self, make_asset, make_employee, monkeypatch):
        asset = make_asset()

        def boom(self, *args, **kwargs):
            raise RuntimeError("asset save failed")

        monkeypatch.setattr(Asset, "save", boom)
        with pytest.raises(RuntimeError):
            services.checkout_asset(
                asset_tag=asset.asset_tag,
                employee_code=make_employee().employee_code,
                due_at=timezone.now() + timedelta(days=1),
            )
        monkeypatch.undo()

        assert CheckOut.objects.count() == 0
        asset.refresh_from_db()
        assert asset.status == Asset.Status.AVAILABLE
