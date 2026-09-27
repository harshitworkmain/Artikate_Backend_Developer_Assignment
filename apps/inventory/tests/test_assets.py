import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.inventory import selectors
from apps.inventory.models import Asset

URL = "/api/v1/assets/"


@pytest.mark.django_db
class TestAssetList:
    def test_filter_by_status_and_category(self, api_client, make_asset):
        make_asset(category=Asset.Category.CAMERA)
        make_asset(category=Asset.Category.CAMERA, status=Asset.Status.MAINTENANCE)
        make_asset(category=Asset.Category.LAPTOP, status=Asset.Status.MAINTENANCE)

        resp = api_client.get(URL, {"status": "MAINTENANCE", "category": "CAMERA"})

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["category"] == "CAMERA"
        assert results[0]["status"] == "MAINTENANCE"

    def test_invalid_filter_value_is_400(self, api_client):
        resp = api_client.get(URL, {"status": "LOST"})

        assert resp.status_code == 400

    def test_search_by_name_and_tag(self, api_client, make_asset):
        make_asset(asset_tag="CAM-001", name="Canon EOS")
        make_asset(asset_tag="LAP-001", name="ThinkPad X1")

        by_name = api_client.get(URL, {"search": "thinkpad"}).json()["results"]
        by_tag = api_client.get(URL, {"search": "CAM-0"}).json()["results"]

        assert [a["asset_tag"] for a in by_name] == ["LAP-001"]
        assert [a["asset_tag"] for a in by_tag] == ["CAM-001"]

    def test_ordering(self, api_client, make_asset):
        make_asset(asset_tag="B-1", name="Zeta")
        make_asset(asset_tag="A-1", name="Alpha")

        default = [a["asset_tag"] for a in api_client.get(URL).json()["results"]]
        by_name_desc = [a["name"] for a in api_client.get(URL, {"ordering": "-name"}).json()["results"]]

        assert default == ["A-1", "B-1"]
        assert by_name_desc == ["Zeta", "Alpha"]

    def test_pagination(self, api_client, make_asset):
        for _ in range(25):
            make_asset()

        page1 = api_client.get(URL).json()
        page2 = api_client.get(URL, {"page": 2}).json()

        assert page1["count"] == 25
        assert len(page1["results"]) == 20
        assert page1["next"] is not None
        assert len(page2["results"]) == 5

    def test_current_holder_on_list(self, api_client, make_asset, make_employee, make_checkout):
        held = make_asset(asset_tag="A-HELD")
        make_asset(asset_tag="B-FREE")
        employee = make_employee(full_name="Ada Lovelace")
        make_checkout(asset=held, employee=employee)

        results = {a["asset_tag"]: a for a in api_client.get(URL).json()["results"]}

        assert results["A-HELD"]["current_holder"] == {
            "employee_code": employee.employee_code,
            "full_name": "Ada Lovelace",
        }
        assert results["B-FREE"]["current_holder"] is None

    def test_list_query_count_does_not_grow_with_rows(self, api_client, make_asset, make_employee, make_checkout):
        def count_queries() -> int:
            with CaptureQueriesContext(connection) as ctx:
                assert api_client.get(URL).status_code == 200
            return len(ctx.captured_queries)

        employee = make_employee()
        make_checkout(asset=make_asset(), employee=employee)
        small = count_queries()
        for _ in range(8):
            make_checkout(asset=make_asset(), employee=make_employee())
        large = count_queries()

        assert small == large


@pytest.mark.django_db
class TestAssetDetail:
    def test_current_holder_null_when_available(self, api_client, make_asset):
        asset = make_asset()

        resp = api_client.get(f"{URL}{asset.pk}/")

        assert resp.status_code == 200
        assert resp.json()["current_holder"] is None

    def test_current_holder_populated_when_checked_out(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        employee = make_employee()
        make_checkout(asset=asset, employee=employee)

        resp = api_client.get(f"{URL}{asset.pk}/")

        assert resp.json()["current_holder"] == {
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        }

    def test_current_holder_cleared_after_return(self, api_client, make_asset, make_employee, make_checkout):
        asset = make_asset()
        checkout = make_checkout(asset=asset, employee=make_employee())
        api_client.post(f"/api/v1/checkouts/{checkout.pk}/return/", {}, format="json")

        resp = api_client.get(f"{URL}{asset.pk}/")

        assert resp.json()["current_holder"] is None


@pytest.mark.django_db
class TestAssetCreate:
    def test_create(self, api_client):
        resp = api_client.post(
            URL,
            {"asset_tag": "NEW-1", "name": "Drone", "category": "SENSOR", "purchase_date": "2025-03-01"},
            format="json",
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "AVAILABLE"
        assert body["current_holder"] is None
        assert Asset.objects.filter(asset_tag="NEW-1").exists()

    def test_duplicate_tag_is_400(self, api_client, make_asset):
        make_asset(asset_tag="DUP-1")

        resp = api_client.post(
            URL,
            {"asset_tag": "DUP-1", "name": "X", "category": "SENSOR", "purchase_date": "2025-03-01"},
            format="json",
        )

        assert resp.status_code == 400
        assert "asset_tag" in resp.json()

    def test_cannot_create_as_checked_out(self, api_client):
        resp = api_client.post(
            URL,
            {"asset_tag": "NEW-2", "name": "X", "category": "SENSOR",
             "purchase_date": "2025-03-01", "status": "CHECKED_OUT"},
            format="json",
        )

        assert resp.status_code == 400
        assert "status" in resp.json()

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_update_and_delete_not_allowed(self, api_client, make_asset, method):
        asset = make_asset()

        resp = getattr(api_client, method)(f"{URL}{asset.pk}/", {}, format="json")

        assert resp.status_code == 405


@pytest.mark.django_db
class TestAuth:
    @pytest.mark.parametrize(
        "method, url",
        [("get", URL), ("post", "/api/v1/checkouts/"), ("post", "/api/v1/checkouts/1/return/")],
    )
    def test_unauthenticated_request_is_401(self, method, url):
        resp = getattr(APIClient(), method)(url, {}, format="json")

        assert resp.status_code == 401
        assert resp.json()["code"] == "not_authenticated"

    def test_obtain_token_and_use_it(self):
        get_user_model().objects.create_user(username="alice", password="s3cret-pass")
        client = APIClient()

        token = client.post("/api/v1/auth/token/", {"username": "alice", "password": "s3cret-pass"}).json()["token"]
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        assert client.get(URL).status_code == 200

    def test_bad_credentials_is_400(self):
        resp = APIClient().post("/api/v1/auth/token/", {"username": "nobody", "password": "x"})

        assert resp.status_code == 400


@pytest.mark.django_db
class TestHealth:
    def test_ok_without_auth(self):
        resp = APIClient().get("/api/v1/health/")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "database": "ok"}

    def test_degraded_when_database_fails(self, monkeypatch):
        monkeypatch.setattr(selectors, "database_is_healthy", lambda: False)

        resp = APIClient().get("/api/v1/health/")

        assert resp.status_code == 503
        assert resp.json() == {"status": "degraded", "database": "error"}

    def test_bad_token_is_ignored(self):
        resp = APIClient(HTTP_AUTHORIZATION="Token not-a-real-token").get("/api/v1/health/")

        assert resp.status_code == 200
