from celery import shared_task
from django.utils import timezone

from . import selectors
from .models import OverdueNotice

ID_CHUNK_SIZE = 2000
INSERT_BATCH_SIZE = 1000


@shared_task
def flag_overdue_checkouts() -> int:
    """Record today's OverdueNotice for every open overdue check-out.

    Safe to run any number of times per day: uniq_notice_per_checkout_per_day
    makes duplicate inserts no-ops (ignore_conflicts), so there is no racy
    "already notified?" pre-check. Returns the number of notices created.
    """
    now = timezone.now()
    today = timezone.localdate(now)
    todays_notices = OverdueNotice.objects.filter(notice_date=today)
    before = todays_notices.count()

    batch: list[OverdueNotice] = []
    ids = selectors.overdue_checkouts(now=now).values_list("id", flat=True)
    for checkout_id in ids.iterator(chunk_size=ID_CHUNK_SIZE):
        batch.append(OverdueNotice(checkout_id=checkout_id, notice_date=today))
        if len(batch) >= INSERT_BATCH_SIZE:
            OverdueNotice.objects.bulk_create(batch, ignore_conflicts=True, batch_size=INSERT_BATCH_SIZE)
            batch = []
    if batch:
        OverdueNotice.objects.bulk_create(batch, ignore_conflicts=True, batch_size=INSERT_BATCH_SIZE)

    # ignore_conflicts can't report how many rows were really inserted.
    return todays_notices.count() - before
