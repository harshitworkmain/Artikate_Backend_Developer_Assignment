from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inventory import services
from apps.inventory.exceptions import AlreadyReturned, NotFound
from apps.inventory.models import Asset, CheckOut


def _url(checkout_id) -> str:
    return f"/api/v1/checkouts/{checkout_id}/return/"


@pytest.mark.django_db
class TestReturnEndpoint:
    def test_return_makes_asset_available(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        checkout = make_checkout(asset=asset, employee=make_employee())

        resp = api_client.post(_url(checkout.pk), {"condition_note": "all good"}, format="json")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == checkout.pk
        assert body["returned_at"] is not None
        assert body["condition_note"] == "all good"
        checkout.refresh_from_db()
        asset.refresh_from_db()
        assert checkout.returned_at is not None
        assert asset.status == Asset.Status.AVAILABLE

    def test_return_with_maintenance_flag(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        checkout = make_checkout(asset=asset, employee=make_employee())

        resp = api_client.post(
            _url(checkout.pk), {"condition_note": "cracked lens", "needs_maintenance": True}, format="json"
        )

        assert resp.status_code == 200
        asset.refresh_from_db()
        assert asset.status == Asset.Status.MAINTENANCE

    def test_empty_body_is_allowed(self, api_client, make_asset, make_employee, make_checkout):
        checkout = make_checkout(asset=make_asset(), employee=make_employee())

        resp = api_client.post(_url(checkout.pk), {}, format="json")

        assert resp.status_code == 200
        assert resp.json()["condition_note"] == ""

    def test_double_return_is_409(self, api_client, make_asset, make_employee, make_checkout):
        checkout = make_checkout(asset=make_asset(), employee=make_employee())
        assert api_client.post(_url(checkout.pk), {}, format="json").status_code == 200

        resp = api_client.post(_url(checkout.pk), {}, format="json")

        assert resp.status_code == 409
        assert resp.json()["code"] == "already_returned"

    def test_unknown_checkout_is_404(self, api_client):
        resp = api_client.post(_url(999999), {}, format="json")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_returned_asset_can_be_checked_out_again(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        checkout = make_checkout(asset=asset, employee=make_employee())
        api_client.post(_url(checkout.pk), {}, format="json")

        resp = api_client.post(
            "/api/v1/checkouts/",
            {"asset_tag": asset.asset_tag, "employee_code": make_employee().employee_code,
             "due_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )

        assert resp.status_code == 201
        assert CheckOut.objects.filter(asset=asset).count() == 2


@pytest.mark.django_db
class TestReturnService:
    def test_sets_returned_at_to_now(self, make_asset, make_employee, make_checkout):
        checkout = make_checkout(asset=make_asset(), employee=make_employee())
        now = timezone.now()

        returned = services.return_checkout(checkout_id=checkout.pk, now=now)

        assert returned.returned_at == now

    def test_unknown_id_raises_not_found(self):
        with pytest.raises(NotFound):
            services.return_checkout(checkout_id=999999)

    def test_already_returned_leaves_asset_untouched(self, make_asset, make_employee, make_checkout):
        asset = make_asset()
        checkout = make_checkout(asset=asset, employee=make_employee(), returned_at=timezone.now())
        Asset.objects.filter(pk=asset.pk).update(status=Asset.Status.MAINTENANCE)

        with pytest.raises(AlreadyReturned):
            services.return_checkout(checkout_id=checkout.pk)

        asset.refresh_from_db()
        assert asset.status == Asset.Status.MAINTENANCE
