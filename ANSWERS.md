# ANSWERS — Parts B, C and D

Parts B and C refer to the Part A models. Where a fix matches something already in this repo, I point to the file.

---

## Part B — Diagnose three broken snippets

### Snippet 1 — overdue report view

**1. What is wrong**

| # | Defect | Effect in production |
|---|--------|----------------------|
| 1 | **N+1 queries.** `c.asset` and `c.employee` are lazy FK loads inside the loop. | 1 + 2N queries. With 5,000 open check-outs that is 10,001 round trips for one page load. |
| 2 | **Overdue filtering happens in Python.** The queryset fetches *every* open check-out, including ones not yet due, and discards them in the loop. | Memory and transfer grow with the number of open check-outs, not with the result. The `due_at` partial index can't be used. |
| 3 | **Sorting happens in Python, on a lossy key.** `days_overdue` is truncated to whole days, so everything overdue by the same number of days ties. `sort()` is stable, so tied rows keep the queryset's order, which is the model default `-checked_out_at`. That order has nothing to do with how overdue a row is. | Within a day bucket, "most overdue first" is wrong. Something 23 h overdue can be listed after something 1 h overdue. |
| 4 | **`timezone.now()` is called 2N times.** The filter and `days_overdue` each call it separately, per row. | There's no single reference time. A row can pass the `<` check against one instant and get its day count from a later one. Rows compared near a day boundary are inconsistent with each other. |
| 5 | **No authentication.** It's a plain Django view, so DRF's `DEFAULT_PERMISSION_CLASSES` never applies, and there's no `login_required`. | Employee names and equipment holdings are readable by anyone who can reach the URL. |
| 6 | **No pagination.** The whole report goes out in one response. | Response size and latency are unbounded. The spec requires 20 per page. |
| 7 | **Missing field.** The spec asks for employee *code* as well as name. | Contract violation. |
| 8 | **No HTTP method restriction.** It accepts POST, PUT and so on. | Minor, but it signals the view is outside the API framework. |

**2. Why it looks correct locally**

- Local data has a handful of rows. Ten check-outs means 21 queries in about 5 ms, so the N+1 problem is invisible without counting queries.
- Defect 2 costs nothing when almost every open check-out in seed data is overdue.
- Ties inside a day bucket need at least two rows overdue by the same whole number of days. With a small hand-made dataset they rarely exist, or happen to come out in the right order.
- Repeated `now()` calls differ by microseconds, so they only matter for a row due in that same microsecond window.
- When you're developing you're logged into the admin anyway, so you never notice the missing auth. Nobody tests the view logged out.

**3. Fix** — this is what `apps/inventory/selectors.py::overdue_checkouts` and `views.py::OverdueReportView` do:

```python
# selectors.py
def overdue_checkouts(*, now=None):
    now = now or timezone.now()                      # one reference instant
    overdue_by = ExpressionWrapper(Value(now, output_field=DateTimeField()) - F("due_at"),
                                   output_field=DurationField())
    return (
        CheckOut.objects.filter(returned_at__isnull=True, due_at__lt=now)   # filter in SQL, uses idx_open_co_due_at
        .select_related("asset", "employee")                                  # one JOINed query
        .annotate(days_overdue=ExtractDay(overdue_by))                        # computed by Postgres
        .order_by("due_at", "id")                                             # exact order + deterministic tiebreak
    )

# views.py — DRF view: IsAuthenticated and PageNumberPagination(20) come from settings
class OverdueReportView(generics.ListAPIView):
    serializer_class = OverdueCheckoutOutputSerializer   # includes employee_code
    filter_backends = []
    def get_queryset(self):
        return selectors.overdue_checkouts()
```

**4. What would have caught it**

- A query-count test that builds 2 rows, then 10 rows, and asserts the counts are equal (`apps/inventory/tests/test_overdue.py::test_query_count_is_constant`). `django_assert_num_queries` or `assertNumQueries` works for this.
- An ordering test with two rows overdue by the same whole number of days.
- An unauthenticated-request test that asserts 401.
- At dev and runtime: django-debug-toolbar or nplusone locally, and Sentry or APM N+1 detection in staging.

---

### Snippet 2 — check-out endpoint

**1. What is wrong**

| # | Defect | Effect in production |
|---|--------|----------------------|
| 1 | **Race between check and write (check-then-act, no lock).** The status check and the create are separate statements with nothing held between them. Two concurrent requests can both read `AVAILABLE` and both create a check-out. | One asset has two open check-outs (breaks rule 7). |
| 2 | **The same race on the three-check-out limit.** Two requests from one employee holding 2 items can both count 2 and both insert. | Employee ends up with 4 open check-outs (breaks rule 3). Locking only the asset wouldn't fix this, because the two requests are for different assets. |
| 3 | **No transaction.** `CheckOut.objects.create` commits on its own (autocommit). If `asset.save()` fails, or the worker is killed between the two statements, the check-out row exists but the asset is still `AVAILABLE`. | Breaks rule 5, and the next request can check the "available" asset out again. |
| 4 | **`asset.save()` without `update_fields`** rewrites *every* column from the stale in-memory copy. | Lost update: if an admin moved the asset to `MAINTENANCE` or renamed it between the read and the save, that change is silently overwritten. |
| 5 | **Unhandled `DoesNotExist`** on both `.get()` calls. | 500 instead of 404 (rule 8). |
| 6 | **`request.data["..."]` with no validation.** | A missing key raises `KeyError` and returns 500. A malformed `due_at` string reaches the model field and raises `ValidationError`, which is also a 500. |
| 7 | **No `due_at` rule.** A past date or one more than 30 days out is accepted. A naive datetime string is interpreted in the server's time zone. | Breaks rule 4. Check-outs can be overdue the moment they're created. |
| 8 | **No inactive-employee check.** | Breaks rule 2. |
| 9 | Magic strings (`"AVAILABLE"`) instead of `Asset.Status`, and the response returns only `{"id"}`. | A typo becomes a silent logic bug, and the response doesn't return the created check-out as the spec requires. |

**2. Why it looks correct locally**

- Manual testing is one request at a time. Reproducing defects 1 and 2 needs two requests to land between the same read and write, which you only get under real concurrency or with a barrier in a test.
- Default Django `TestCase` runs each test inside one transaction on one connection, so a race *can't* occur there. And on SQLite, even correct `select_for_update()` does nothing.
- Defect 3 needs a failure between two statements a few milliseconds apart.
- Defect 4 needs a concurrent writer to the same row.
- Happy-path tests always use valid tags, codes and dates, so the 500s never show up.

**3. Fix** — this is the design in `apps/inventory/services.py::checkout_asset`, with the view in `views.py::CheckOutViewSet.create`:

```python
# views.py
def create(self, request):
    data = CheckOutInputSerializer(data=request.data)      # missing/malformed -> 400, due_at parsed tz-aware
    data.is_valid(raise_exception=True)
    checkout = services.checkout_asset(**data.validated_data)
    return Response(CheckOutOutputSerializer(checkout).data, status=201)

# services.py
def checkout_asset(*, asset_tag, employee_code, due_at, now=None):
    now = now or timezone.now()
    if not (now < due_at <= now + timedelta(days=30)):
        raise InvalidDueDate()                                              # 400
    with transaction.atomic():                                              # rule 5: all or nothing
        try:
            employee = Employee.objects.select_for_update().get(employee_code=employee_code)
        except Employee.DoesNotExist:
            raise NotFound(...)                                             # 404
        if not employee.is_active:
            raise InactiveEmployee()                                        # 400
        try:  # lock order is always employee -> asset, so no deadlock cycle is possible
            asset = Asset.objects.select_for_update().get(asset_tag=asset_tag)
        except Asset.DoesNotExist:
            raise NotFound(...)                                             # 404
        if asset.status != Asset.Status.AVAILABLE:
            raise AssetUnavailable()                                        # 409
        if CheckOut.objects.filter(employee=employee, returned_at__isnull=True).count() >= 3:
            raise CheckoutLimitReached()                                    # 409
        try:
            with transaction.atomic():                                      # savepoint
                checkout = CheckOut.objects.create(asset=asset, employee=employee, due_at=due_at)
        except IntegrityError as exc:                                       # uniq_open_checkout_per_asset backstop
            if constraint_name(exc) == "uniq_open_checkout_per_asset":
                raise AssetUnavailable()
            raise
        asset.status = Asset.Status.CHECKED_OUT
        asset.save(update_fields=["status", "updated_at"])                 # no lost update
    return checkout
```

The row lock on the asset makes the second request wait. It then re-reads `CHECKED_OUT` and gets a 409. The lock on the employee row serialises one employee's check-outs, so the count can't be raced. The partial unique index `(asset) WHERE returned_at IS NULL` is a database-level guarantee: even if some future code path skips the service, Postgres refuses a second open check-out.

**4. What would have caught it**

- A `transaction=True` pytest against real Postgres, with two threads released by a `threading.Barrier`, that asserts exactly one 201 and one 409. Plus the same-employee variant. (`apps/inventory/tests/test_concurrency.py`, both run 10× without flaking.)
- A test that monkeypatches `Asset.save` to raise after the create, then asserts no `CheckOut` row exists (`test_checkout.py::test_failure_after_create_rolls_back_everything`).
- Schema fuzzing (e.g. schemathesis) against the OpenAPI spec, which catches every path to a 500.
- The DB constraint itself, which turns a silent double-booking into a loud `IntegrityError`.

---

### Snippet 3 — nightly notice task

**1. What is wrong**

| # | Defect | Effect in production |
|---|--------|----------------------|
| 1 | **Not idempotent.** A plain `create()` per row. With the Part A unique constraint, a second run the same day raises `IntegrityError` on the *first* row that already has a notice. Without the constraint, it inserts duplicates. | Retry after partial failure: the rows that already succeeded now raise, so the retry dies at the same point every time and the remaining employees are **never** notified that day. Without the constraint, everyone before the failure point gets a duplicate notice. |
| 2 | **Emails aren't tied to notice creation.** `deliver_email.delay` fires for every overdue row whether or not a notice was actually created by this run. | Every retry or manual re-run emails everyone again. |
| 3 | **Emails are enqueued outside any transaction boundary.** If this task ran inside `atomic()` and rolled back, the broker messages would already be published. | An email goes out about a notice that doesn't exist. The fix is `transaction.on_commit`. |
| 4 | **Model instances are passed to `.delay()`.** With Celery's default JSON serializer, `Employee`/`CheckOut` objects raise `EncodeError`. With pickle, the worker gets a stale snapshot, and pickle from a broker is a security risk. | The task fails on the first row in a real worker. |
| 5 | **N+1.** `c.employee` is lazily loaded per row. | Tens of thousands of extra queries. |
| 6 | **Row-by-row work at scale.** The queryset result cache holds every instance in memory. There's one INSERT and one broker publish per row. | With tens of thousands of rows, memory spikes and the task runs for minutes. If `acks_late` is enabled and the run exceeds the Redis broker's `visibility_timeout`, the message is redelivered and a *second copy* runs concurrently, making defects 1 and 2 worse. |
| 7 | **`overdue.count()` is a separate, later query.** | It reports how many are overdue *now*, not how many notices were sent. The log line is misleading. |
| 8 | **`timezone.now()` is re-evaluated per row** for `notice_date`. | A run that crosses midnight UTC stamps notices with two different dates, so the per-day uniqueness no longer lines up with a single run. |
| 9 | **No retry policy.** A transient DB or broker error just fails the task. | Combined with defect 1, a retry isn't safe anyway. |

**2. Why it looks correct locally**

- It runs once per test, on an empty `OverdueNotice` table, so the duplicate or constraint path never runs.
- Local Celery is usually `CELERY_TASK_ALWAYS_EAGER=True`. Eager tasks run in-process and **skip serialization**, so passing model instances works. The same code fails in a real worker.
- There are a few rows, so the N+1 queries, memory use and task duration don't show.
- Nobody runs it twice on the same day, or kills it halfway through.

**3. Fix** — insert and learn which rows were *newly* inserted in one statement, then enqueue only those, after commit, by id:

```python
from django.db import connection, transaction, OperationalError

@shared_task(bind=True, autoretry_for=(OperationalError,), retry_backoff=True, max_retries=5)
def send_overdue_notices(self):
    now = timezone.now()
    today = timezone.localdate(now)                       # one date for the whole run
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO inventory_overduenotice (checkout_id, notice_date, created_at)
                SELECT id, %s, now()
                FROM inventory_checkout
                WHERE returned_at IS NULL AND due_at < %s
                ON CONFLICT (checkout_id, notice_date) DO NOTHING
                RETURNING id
                """,
                [today, now],
            )
            new_ids = [row[0] for row in cur.fetchall()]  # only notices THIS run created

        def enqueue():
            for i in range(0, len(new_ids), 500):          # batch publishes; ids, not objects
                deliver_overdue_emails.delay(new_ids[i:i + 500])

        transaction.on_commit(enqueue)
    return len(new_ids)
```

- A single set-based INSERT does the work in one round trip, with no Python loop over tens of thousands of rows. `ON CONFLICT DO NOTHING` makes re-runs and retries no-ops. `RETURNING` tells us exactly which notices are new, so emails aren't duplicated. `on_commit` means no email goes out for a rolled-back notice.
- Remaining gap, stated honestly: if the process dies *after* commit but *before* `enqueue` runs, those notices exist but their emails were never queued, and a retry won't re-send them because of the conflict. The complete fix is a transactional outbox: an `emailed_at` column on `OverdueNotice`, with `deliver_overdue_emails` setting it, and a sweeper that re-queues `emailed_at IS NULL`. That's an extra field, so I'd add it with a note rather than silently change the prescribed schema. `deliver_overdue_emails` itself must also be idempotent, keyed on notice id.
- In this repo's Part A task (`apps/inventory/tasks.py`) no email is involved, so `bulk_create(ignore_conflicts=True)` over an id iterator is enough.

**4. What would have caught it**

- A test that runs the task twice and asserts one notice per check-out and the email task enqueued once (mock `.delay`) (`test_tasks.py::test_second_run_is_a_no_op` covers the notice half).
- A test that raises partway through the first run, re-runs, and asserts everyone is notified exactly once.
- Running tests with `task_always_eager=False` against a real broker in CI (the compose job), or asserting `kombu.serialization.dumps(args, serializer="json")` succeeds, to catch the serialization failure.
- A volume test with 20k overdue rows asserting a constant query count and a time budget.
- In production: Flower or task-duration metrics, and an alert when duration approaches `visibility_timeout`.

---

## Part C — Optimise a slow PostgreSQL query

To check my reasoning, I built a synthetic `checkouts` table locally on PostgreSQL 16 with 4.2M rows over 525 days at 8,000/day, 12k employees with 90% active, and about 2.5% of rows open (recent unreturned items plus 1% never returned). That gives 105,540 open rows in total and 14,447 in the Jan–Jun window. The data shape is my guess, not production's, so the absolute timings below are illustrative. What matters is the plan shapes and the buffer counts.

### 1. Rewritten query

```sql
SELECT c.id, c.asset_id, c.employee_id, c.checked_out_at, c.due_at   -- only what the screen shows
FROM checkouts c
WHERE c.checked_out_at >= TIMESTAMPTZ '2026-01-01 00:00:00+00'
  AND c.checked_out_at <  TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND c.returned_at IS NULL
  AND EXISTS (SELECT 1 FROM employees e WHERE e.id = c.employee_id AND e.is_active)
ORDER BY c.due_at, c.id;
```

| Change | Gain | Cost / caveat |
|--------|------|---------------|
| `DATE(c.checked_out_at) BETWEEN …` → half-open range on the raw column | The predicate becomes sargable, so a btree on `checked_out_at` can be used. `DATE(timestamptz)` depends on the session's `TimeZone`, so it's only STABLE, not IMMUTABLE: you **can't even build an expression index on it**. It also fixes a correctness bug: the old query returned different rows depending on the client's `TimeZone` setting. And the planner can't estimate selectivity through a function (in my run it estimated 238 rows per worker against 4,816 actual), which pushes it toward the wrong join strategy. | I have to decide which time zone "2026-01-01" means. I assumed UTC. If the business means IST, the bounds become `'2026-01-01 00:00+05:30'`. |
| `BETWEEN '…' AND '2026-06-30'` → `< '2026-07-01'` | Includes all of 30 June without relying on day truncation. It's the standard half-open idiom. | None. |
| `SELECT *` → explicit columns | Avoids detoasting `condition_note` and cuts row width (155 → 40 bytes in the plan). It also makes an index-only scan possible (below). | If the screen really needs `condition_note`, add it back, but then an index-only scan is off the table. |
| `IN (subquery)` → `EXISTS` | Honestly, small. Postgres already plans a non-`NOT` `IN` as a semi-join. I changed it for clarity and so the planner has an easy hash semi-join. The employee filter isn't selective (≈90% active), so it shouldn't drive the plan. | None. I'd skip this change if reviewers preferred the original form. |
| `ORDER BY due_at` → `due_at, id` | Deterministic order when `due_at` ties, which matters if the screen ever paginates. | None. |

The rewrite alone didn't make it fast: 289 ms → 224 ms in my run, still a parallel seq scan over 4.2M rows. The index is what makes the difference.

### 2. Indexes

```sql
CREATE INDEX CONCURRENTLY idx_checkouts_open_checked_out_at
    ON checkouts (checked_out_at)
    INCLUDE (id, due_at, employee_id, asset_id)
    WHERE returned_at IS NULL;
```

- **Why partial:** every query of this kind has `returned_at IS NULL`, and open rows are a small slice of the table (~2.5% in my model). The partial index was **6 MB against 90 MB** for a full index on `checked_out_at`. Its size tracks open check-outs, not table history, so it stays small as the table grows. A returned row drops out of the index.
- **Why `checked_out_at` as the key:** it's the range predicate that does the filtering. Postgres range-scans the open rows in the window (14k) and never touches the other 4.18M.
- **Why `INCLUDE`:** with the selected columns stored in the leaf pages, Postgres can do an **Index Only Scan** and skip the heap entirely, as long as the visibility map is current. The cost: a wider index and slightly more write amplification on insert. It's only worth it if the screen really drops `SELECT *`. Without `INCLUDE`, my run did a plain Index Scan reading ~12k heap pages (82 ms). With it, 108 index pages (5.9 ms).
- **Why not composite `(returned_at, checked_out_at)`:** it indexes all 4.2M rows to serve a predicate that only needs 2.5% of them. It's 15× bigger for no benefit over the partial one.
- **Considered: partial `(due_at) WHERE returned_at IS NULL`.** It returns rows already in `ORDER BY` order, so no sort, and it would win if the screen used `LIMIT` or pagination. Part A has exactly this index for the overdue report, where `due_at` *is* the filter. Here there's no `LIMIT`, so it would scan all 105k open rows to filter the date range, and sorting 14k rows in memory costs about 1 ms (`quicksort Memory: 1197kB`). The `checked_out_at` index wins for this query.
- **Not adding:** an index on `employees(is_active)`. 12k rows, ~100 pages, 90% true: a seq scan plus hash is the cheapest plan and an index would never be chosen.
- `CONCURRENTLY` because this is a live table. It can't run inside a transaction block, and if it fails it leaves an `INVALID` index that must be dropped and recreated.

### 3. EXPLAIN (ANALYZE, BUFFERS) before and after

**Before** (from my run, abbreviated):
```
Gather Merge
  -> Sort  Sort Key: c.due_at
    -> Nested Loop
      -> Parallel Seq Scan on checkouts c  (rows=238 estimated) (actual rows=4816 loops=3)
           Filter: ((returned_at IS NULL) AND (date(checked_out_at) >= ...) AND (date(checked_out_at) <= ...))
           Rows Removed by Filter: 1395184
           Buffers: shared hit=15948 read=80553
      -> Index Scan using employees_pkey on employees (loops=14447)   -- 43k buffer hits from the misestimate
Buffers: shared hit=59307 read=80553        (~140k pages ≈ 1.1 GB touched)
Execution Time: 289 ms
```

**After** (rewrite + index):
```
Sort  Sort Key: c.due_at  Sort Method: quicksort  Memory: 1197kB
  -> Hash Join  Hash Cond: (c.employee_id = e.id)
    -> Index Only Scan using idx_checkouts_open_checked_out_at on checkouts c (actual rows=14447)
         Index Cond: ((checked_out_at >= ...) AND (checked_out_at < ...))
         Heap Fetches: 0
         Buffers: shared hit=2 read=106
    -> Hash -> Seq Scan on employees e  Filter: is_active
Buffers: shared hit=201 read=14
Execution Time: 5.9 ms
```

**The line that proves the fix worked** is the scan node on `checkouts`. `Parallel Seq Scan … Rows Removed by Filter: 1395184` becomes `Index Only Scan using idx_checkouts_open_checked_out_at … Index Cond: (checked_out_at …)`, and that node's `Buffers` drops from ~96k pages to ~108.

Two secondary checks:
- `Heap Fetches` should be near 0. If it's high, the visibility map is stale, and a VACUUM, not another index, is the fix.
- The row estimate should be close to the actual count. It was 238 against 4,816 before and roughly right after.

Note: locally the "before" took 289 ms, not 8 s, because my data was warm in cache on a fast SSD with 2 parallel workers. Production's 8 s is therefore probably dominated by cold I/O (~1.1 GB of pages read per run), table bloat, or result transfer. See question 5.

### 4. What breaks first as the table grows (+8,000/day ≈ +2.9M/year)

1. **First: statistics and vacuum lag on recent data.** Autovacuum and autoanalyze trigger on a *fraction* of the table (defaults `vacuum_scale_factor=0.2`, `analyze_scale_factor=0.1`). At 4.2M rows that's ~420k changed rows before an ANALYZE, about 52 days of inserts, and the gap keeps growing. Meanwhile the newest `checked_out_at` values lie past the end of the histogram, so any "recent window" query gets misestimated, and the visibility map on new pages goes stale, which turns index-only scans back into heap fetches. The first thing to go is plan stability, well before raw size is a problem.
   → Set per-table `ALTER TABLE checkouts SET (autovacuum_analyze_scale_factor = 0.005, autovacuum_vacuum_scale_factor = 0.01);`, or absolute thresholds, and alert on `last_autoanalyze` age.
2. **Next: full-table operations.** Anything without the open predicate (history reports), anti-wraparound VACUUM freeze passes over the whole table, index builds, and backup and restore time all grow linearly.
   → Before it gets to the tens of millions, **range-partition by `checked_out_at` (monthly)**. Date-window queries get partition pruning, VACUUM works per partition, and old partitions can be detached and archived instead of deleted. The trade-off: the primary key has to include the partition key (`(id, checked_out_at)`), which complicates the `OverdueNotice` FK, and Django needs raw-SQL migrations for this. That's why I'd do it early rather than retrofit it at 50M rows.
3. The partial index itself isn't the risk: it grows with *open* rows. The exception is if returns stop being recorded, and then that's a data problem worth alerting on in its own right.

### 5. What I'd measure on the real database first

**The actual share of rows with `returned_at IS NULL`, overall and inside the date window** (`SELECT count(*) FILTER (WHERE returned_at IS NULL) …` or `pg_stats.null_frac`). My whole index design rests on open rows being a small minority. If returns aren't reliably recorded and 60% of rows are "open", the partial index is barely smaller than a full one. With millions of qualifying rows, a seq scan might even be the right plan, and the real fix would be pagination on the screen.

I'd also run `EXPLAIN (ANALYZE, BUFFERS)` on a production replica. The same query shape ran in 289 ms on my synthetic data, so I can't tell from here whether production's 8 s is disk reads, bloat, or the client pulling many wide rows. Each of those has a different fix.

---

## Part D — Production reasoning

### D1. Zero-downtime migration: non-nullable `location_id` FK on 4.2M-row `checkouts`

This is expand/contract: **3 deploys plus a backfill job**, with migrations run *before* the new code goes live in each deploy.

**Deploy 1 (migration only, expand).** `ALTER TABLE checkouts ADD COLUMN location_id bigint NULL;` Adding a nullable column with no default is metadata-only, but it still needs a brief `ACCESS EXCLUSIVE` lock. I set `lock_timeout = '3s'` and retry. Otherwise the ALTER queues behind any long transaction, and every read queued behind *it* stalls too. Then `ADD CONSTRAINT … FOREIGN KEY (location_id) REFERENCES locations(id) NOT VALID` (instant), and `CREATE INDEX CONCURRENTLY` in a separate migration with `atomic = False`. By default Django's `AddField` for a FK would create the index non-concurrently and validate the FK inline, blocking writes, so I'd check `sqlmigrate` and split it with `SeparateDatabaseAndState` / `AddIndexConcurrently`. In-flight old code: Django selects explicit columns and old inserts omit `location_id`, so NULL is accepted and nothing breaks.

**Deploy 2 (code).** The model gets `location_id` with `null=True`, and every create or update writes it. During the rolling restart across the 4 instances, old instances still write NULL, which is legal.

**Backfill (a job, not a deploy).** Update in id-range batches of about 5k rows, one transaction per batch, sleeping between batches and watching replication lag. Then re-run until `WHERE location_id IS NULL` returns zero; that also catches rows old instances wrote during deploy 2. `VALIDATE CONSTRAINT` on the FK takes only `SHARE UPDATE EXCLUSIVE`, so reads and writes continue.

**Deploy 3 (migration, contract).** `ADD CONSTRAINT location_id_nn CHECK (location_id IS NOT NULL) NOT VALID;` → `VALIDATE CONSTRAINT location_id_nn;` → `ALTER COLUMN location_id SET NOT NULL;` (PG 12+ uses the validated check to skip the table scan) → drop the check. The model becomes `null=False`. Deploy-2 code already writes the column, so the old and new code are both safe.

**What would lock the table if I got it wrong:** `ALTER TABLE … ALTER COLUMN location_id SET NOT NULL` (or `ADD COLUMN … NOT NULL` straight away) without a pre-validated CHECK. That takes an **ACCESS EXCLUSIVE** lock and scans all 4.2M rows while holding it, so every read and write on `checkouts` blocks until the scan finishes. The close runner-up is Django's default inline FK validation, `SHARE ROW EXCLUSIVE`, which blocks writes on both tables.

### D2. Latency triage: `/api/v1/reports/overdue/` at 25 s, no deploy in 9 days

In order, each check narrowing the search:

1. **Is it only this endpoint?** Compare APM p95 across endpoints, plus DB CPU and I/O. If everything is slow, it's the database or infrastructure (disk burst credits exhausted, a noisy neighbour, a failover). If only this endpoint, it's this query or its data.
2. **Where is the time spent: SQL or Python?** From the APM trace spans. The response is paginated to 20 rows, so a slow serializer is unlikely. I expect SQL, but I'd confirm it.
3. **Is the query waiting or working?** Look at `pg_stat_activity` while the endpoint runs. `wait_event_type = Lock` means something is blocking it. Also check `pg_stat_statements` for this query's `mean_exec_time` trend: a sudden step change or a gradual slope.
4. **`EXPLAIN (ANALYZE, BUFFERS)` of the exact SQL, compared with what it should be:** an index scan on `idx_open_co_due_at`. Has the plan flipped to a seq scan? Are estimated and actual rows wildly different? Are the buffer counts huge relative to the rows returned?
5. **Data volume:** how many open overdue rows are there now compared with a week ago? The report's result set grows with the passage of time, with no writes needed. If returns stopped being recorded (a broken integration upstream), the overdue set and the pagination `COUNT(*)` grow every day.
6. **Table health:** `pg_stat_user_tables` for `n_dead_tup`, `last_autovacuum` and `last_autoanalyze`; the oldest `xact_start` or `backend_xmin` in `pg_stat_activity`; and `pg_replication_slots`.

**The two most likely causes, given no code change:**

- **Stale statistics causing a plan flip.** Autoanalyze hasn't fired on a large, growing table (the threshold is a fraction of the table), the planner misestimates the open/overdue rows, and it switches away from the partial index. *Confirm:* in step 4, a large estimated-vs-actual row mismatch and an old `last_autoanalyze`. Run `ANALYZE checkouts` and the plan and latency should snap back. Then lower the per-table scale factors so it doesn't recur.
- **Bloat because vacuum is being held back.** A long-running or `idle in transaction` session, or an abandoned replication slot, pins `xmin`, so VACUUM can't remove dead tuples. Every return deletes an entry from the partial index, so that index fills with dead entries and each scan reads far more pages than it returns. *Confirm:* a very old `backend_xmin` or `xact_start`, or an inactive slot, rising `n_dead_tup`, and buffer counts far above what the row count justifies. Terminate the offender, let VACUUM catch up (REINDEX CONCURRENTLY if needed), and add `idle_in_transaction_session_timeout`.

### D3. CI/CD on GitHub Actions

**On every PR (required checks, branch protection, one review):**
- ruff lint and format check, and `manage.py check --deploy`.
- `makemigrations --check --dry-run`, so no model change ships without its migration.
- A migration-safety lint: run `sqlmigrate` for new migrations through squawk or django-migration-linter, and fail on non-concurrent index creation, `SET NOT NULL`, inline FK validation, column drops or renames.
- pytest against a **real Postgres service container**. The concurrency tests are meaningless on SQLite.
- `docker build`, the compose smoke job (already in `.github/workflows/ci.yml`: up, migrate, seed, health, authenticated call, worker ping), and pip-audit.

**On merge to main:**
- Build the image **once**, tagged with the commit SHA, and push it to the registry.
- Deploy that exact image to staging: run migrations, run a smoke test (health, token auth, a check-out and return on a test asset), and apply the same migrations to a copy of production-sized data to time them.

**Production gate:** a GitHub Environment with required reviewers. The input is the SHA that passed staging, never a rebuild.

**Order at deploy time:**
1. Run `manage.py migrate` as a one-off job using the *new* image, before any app instance changes. This is only safe because of a rule enforced in review and linting: every migration must be compatible with the currently running code (expand only; D1 is the pattern).
2. Rolling-deploy the web instances, with `/api/v1/health/` as the readiness check.
3. Restart Celery workers gracefully (warm shutdown, so in-flight tasks finish), and restart beat as a single instance.
4. Watch error rate and p95 for a set window; roll back automatically if thresholds are breached.

**Rollback after the schema has already migrated:** roll back **code, not schema**. Every migration is expand-only, so the previous image runs fine against the new schema; rollback means redeploying the previous SHA. I don't run reverse migrations in production. They're often lossy (dropping a column the new code has already written to), and they're the least-tested code path. Destructive changes (drops, `NOT NULL`, renames) ship as a *separate, later* contract release, once no running code references the old shape. For a field removal, first a deploy that drops it from Django's state only, then a deploy that drops the column. If a migration itself was wrong (for example a bad backfill), the answer is a forward fix. Point-in-time restore is the last resort, because it loses every write made since the restore point.