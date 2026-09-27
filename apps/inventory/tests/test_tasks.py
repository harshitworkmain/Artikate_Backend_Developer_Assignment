from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.conf import settings
from django.utils import timezone

from apps.inventory import tasks
from apps.inventory.models import OverdueNotice
from apps.inventory.tasks import flag_overdue_checkouts


@pytest.fixture
def mixed_checkouts(make_asset, make_employee, make_checkout):
    now = timezone.now()
    employee = make_employee()
    overdue = [
        make_checkout(asset=make_asset(), employee=employee, due_at=now - timedelta(days=2)),
        make_checkout(asset=make_asset(), employee=employee, due_at=now - timedelta(hours=1)),
    ]
    not_due = make_checkout(asset=make_asset(), employee=make_employee(), due_at=now + timedelta(days=1))
    returned = make_checkout(
        asset=make_asset(), employee=make_employee(),
        due_at=now - timedelta(days=5), returned_at=now - timedelta(days=1),
    )
    return overdue, not_due, returned


@pytest.mark.django_db
class TestFlagOverdueCheckouts:
    def test_creates_one_notice_per_overdue_checkout(self, mixed_checkouts):
        overdue, not_due, returned = mixed_checkouts
        today = timezone.localdate()

        assert flag_overdue_checkouts() == 2

        notices = OverdueNotice.objects.filter(notice_date=today)
        assert sorted(notices.values_list("checkout_id", flat=True)) == sorted(c.pk for c in overdue)
        assert not OverdueNotice.objects.filter(checkout__in=[not_due, returned]).exists()

    def test_second_run_is_a_no_op(self, mixed_checkouts):
        overdue, _, _ = mixed_checkouts

        assert flag_overdue_checkouts() == 2
        assert flag_overdue_checkouts() == 0

        assert OverdueNotice.objects.count() == 2
        for checkout in overdue:
            assert checkout.notices.count() == 1

    def test_yesterdays_notice_does_not_block_today(self, mixed_checkouts):
        overdue, _, _ = mixed_checkouts
        today = timezone.localdate()
        OverdueNotice.objects.create(checkout=overdue[0], notice_date=today - timedelta(days=1))

        assert flag_overdue_checkouts() == 2

        assert set(overdue[0].notices.values_list("notice_date", flat=True)) == {today - timedelta(days=1), today}

    def test_nothing_overdue(self, make_asset, make_employee, make_checkout):
        make_checkout(asset=make_asset(), employee=make_employee(), due_at=timezone.now() + timedelta(days=1))

        assert flag_overdue_checkouts() == 0
        assert not OverdueNotice.objects.exists()

    def test_batches_larger_than_insert_batch_size(self, mixed_checkouts, monkeypatch):
        monkeypatch.setattr(tasks, "INSERT_BATCH_SIZE", 1)

        assert flag_overdue_checkouts() == 2
        assert flag_overdue_checkouts() == 0

    def test_runs_as_celery_task_without_broker(self, mixed_checkouts):
        result = flag_overdue_checkouts.apply()

        assert result.successful()
        assert result.get() == 2


def test_beat_schedule_is_hourly_and_points_at_the_task():
    entry = settings.CELERY_BEAT_SCHEDULE["flag-overdue-checkouts-hourly"]

    assert entry["task"] == flag_overdue_checkouts.name
    assert entry["schedule"] == crontab(minute=0)
