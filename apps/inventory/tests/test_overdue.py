from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.inventory import selectors

URL = "/api/v1/reports/overdue/"


@pytest.mark.django_db
class TestOverdueSelector:
    def test_inclusion_days_and_order(self, make_asset, make_employee, make_checkout):
        now = timezone.now()
        employee = make_employee()
        three_days = make_checkout(asset=make_asset(), employee=employee, due_at=now - timedelta(days=3))
        one_hour = make_checkout(asset=make_asset(), employee=employee, due_at=now - timedelta(hours=1))
        almost_two = make_checkout(
            asset=make_asset(), employee=make_employee(), due_at=now - timedelta(days=1, hours=23)
        )
        make_checkout(asset=make_asset(), employee=make_employee(), due_at=now)
        make_checkout(asset=make_asset(), employee=make_employee(), due_at=now + timedelta(days=1))
        make_checkout(
            asset=make_asset(), employee=make_employee(),
            due_at=now - timedelta(days=5), returned_at=now - timedelta(days=1),
        )

        rows = list(selectors.overdue_checkouts(now=now))

        assert [r.pk for r in rows] == [three_days.pk, almost_two.pk, one_hour.pk]
        assert [r.days_overdue for r in rows] == [3, 1, 0]

    def test_single_query_including_related(self, make_asset, make_employee, make_checkout,
                                            django_assert_num_queries):
        now = timezone.now()
        for _ in range(5):
            make_checkout(asset=make_asset(), employee=make_employee(), due_at=now - timedelta(days=1))

        with django_assert_num_queries(1):
            rows = list(selectors.overdue_checkouts(now=now))
            names = [(r.asset.name, r.asset.asset_tag, r.employee.full_name) for r in rows]

        assert len(names) == 5


@pytest.mark.django_db
class TestOverdueEndpoint:
    def test_rows(self, api_client, make_asset, make_employee, make_checkout):
        now = timezone.now()
        asset = make_asset(name="Canon EOS")
        employee = make_employee(full_name="Ada Lovelace")
        checkout = make_checkout(asset=asset, employee=employee, due_at=now - timedelta(days=3, hours=2))
        make_checkout(asset=make_asset(), employee=make_employee(), due_at=now + timedelta(days=1))

        resp = api_client.get(URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["checkout_id"] == checkout.pk
        assert row["asset_name"] == "Canon EOS"
        assert row["asset_tag"] == asset.asset_tag
        assert row["employee_code"] == employee.employee_code
        assert row["employee_name"] == "Ada Lovelace"
        assert row["days_overdue"] == 3
        assert set(row) == {
            "checkout_id", "asset_name", "asset_tag", "employee_code",
            "employee_name", "due_at", "days_overdue",
        }

    def test_paginated(self, api_client, make_asset, make_employee, make_checkout):
        now = timezone.now()
        for i in range(22):
            make_checkout(asset=make_asset(), employee=make_employee(), due_at=now - timedelta(days=i + 1))

        page1 = api_client.get(URL).json()

        assert page1["count"] == 22
        assert len(page1["results"]) == 20
        assert page1["results"][0]["days_overdue"] == 22

    def test_query_count_is_constant(self, api_client, make_asset, make_employee, make_checkout):
        now = timezone.now()

        def add(n: int) -> None:
            for _ in range(n):
                make_checkout(asset=make_asset(), employee=make_employee(), due_at=now - timedelta(days=1))

        def count_queries() -> int:
            with CaptureQueriesContext(connection) as ctx:
                assert api_client.get(URL).status_code == 200
            return len(ctx.captured_queries)

        add(2)
        with_two = count_queries()
        add(8)
        with_ten = count_queries()

        assert with_two == with_ten
