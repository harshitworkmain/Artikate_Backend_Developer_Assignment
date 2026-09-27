# Field Asset Check-Out Service

A Django REST API for tracking equipment that employees check out and return. Built for the Artikate Backend Developer take-home.

- **Stack:** Python 3.12, Django 5.1, Django REST Framework, django-filter, PostgreSQL 16, Celery + Redis, pytest-django
- **Auth:** DRF **Token** authentication (Session auth is also enabled for the browsable API). Everything except `/api/v1/health/` requires a token.
- **Written answers for Parts B, C and D:** [`ANSWERS.md`](ANSWERS.md)
- **Screen recording:** _<add link>_

---

## 1. Run with Docker (API, PostgreSQL, Redis, Celery worker, Celery beat)

```bash
git clone https://github.com/harshitworkmain/Artikate_Backend_Developer_Assignment.git
cd Artikate_Backend_Developer_Assignment

docker compose up -d --build                                   # web, db, redis, worker, beat
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo_data         # prints the API token

curl http://localhost:8000/api/v1/health/
```

No `.env` is needed for Docker; `docker-compose.yml` has working defaults. `POSTGRES_PASSWORD`, `SECRET_KEY` and `DEBUG` can be overridden from the shell or a `.env` file.

## 2. Run locally without Docker

Needs PostgreSQL 16 running locally, with a database called `assetsvc` (`createdb assetsvc`, or `CREATE DATABASE assetsvc;` in psql). Redis is **not** needed to run the API or the tests.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # Windows: copy .env.example .env
# edit DATABASE_URL in .env, e.g. postgres://postgres:<password>@localhost:5432/assetsvc

python manage.py migrate
python manage.py seed_demo_data
python manage.py runserver
```

Optional, if Redis is available: `celery -A config worker -l info` and `celery -A config beat -l info`.

## 3. Tests

```bash
pytest -q                            # local: uses DATABASE_URL, creates its own test_ database
docker compose exec web pytest -q    # inside the stack
```

The suite has 82 tests and must run against PostgreSQL, because the concurrency tests rely on real row locks. It covers everything listed in A5:

| Requirement | Test |
|---|---|
| Concurrency: two simultaneous check-outs of one asset → exactly one 201 and one 409 | `tests/test_concurrency.py` (threads + `Barrier`, `transaction=True`). There's also a same-employee race against the 3-check-out limit |
| Three-open-check-outs limit | `tests/test_checkout.py::test_fourth_open_checkout_is_409`, `::test_limit_counts_only_open_checkouts` |
| Overdue calculation incl. an item due exactly now | `tests/test_overdue.py`, `tests/test_summary.py::test_due_exactly_now_is_not_overdue` |
| Employee summary's four numbers + single query | `tests/test_summary.py` (`django_assert_num_queries(1)`) |
| Background task idempotency | `tests/test_tasks.py::test_second_run_is_a_no_op` |

CI (`.github/workflows/ci.yml`) runs on every push. The `test` job runs checks, `makemigrations --check` and pytest on a Postgres 16 service. The `compose` job runs `docker compose up --build`, then migrate, seed, a health check and an authenticated request, and checks the worker responds to `celery inspect ping`.

---

## 4. Using the API

```bash
# get a token (the seed command also prints it)
curl -X POST -d "username=demo&password=demo-password" http://localhost:8000/api/v1/auth/token/
export T="Authorization: Token <token>"
```

| Method & path | Notes |
|---|---|
| `POST /api/v1/auth/token/` | `username`, `password` → `{"token": ...}` |
| `GET  /api/v1/health/` | No auth. `200 {"status":"ok","database":"ok"}` or `503` |
| `POST /api/v1/assets/` | `asset_tag, name, category, purchase_date[, status]` |
| `GET  /api/v1/assets/?status=&category=&search=&ordering=&page=` | 20 per page; `search` matches name or asset_tag |
| `GET  /api/v1/assets/{id}/` | includes `current_holder`: `null` or `{employee_code, full_name}` |
| `POST /api/v1/checkouts/` | `{asset_tag, employee_code, due_at}` → 201 |
| `POST /api/v1/checkouts/{id}/return/` | `{condition_note, needs_maintenance}` → 200 |
| `GET  /api/v1/employees/{employee_code}/summary/` | `lifetime_checkouts, currently_held, currently_overdue, mean_hold_days` |
| `GET  /api/v1/reports/overdue/` | most overdue first, paginated |

```bash
curl -H "$T" "http://localhost:8000/api/v1/assets/?category=LAPTOP&search=dell"
curl -H "$T" -H "Content-Type: application/json" \
     -d '{"asset_tag":"DEMO-LAP-03","employee_code":"DEMO-E002","due_at":"2026-10-05T10:00:00Z"}' \
     http://localhost:8000/api/v1/checkouts/
curl -H "$T" -H "Content-Type: application/json" -d '{"needs_maintenance":true,"condition_note":"cracked hinge"}' \
     http://localhost:8000/api/v1/checkouts/<id>/return/
curl -H "$T" http://localhost:8000/api/v1/employees/DEMO-E001/summary/
curl -H "$T" http://localhost:8000/api/v1/reports/overdue/
```

Errors always have the shape `{"detail": "...", "code": "..."}`. For example: `asset_unavailable` (409), `checkout_limit_reached` (409), `already_returned` (409), `inactive_employee` (400), `invalid_due_date` (400), `not_found` (404).

**Seed data** (`seed_demo_data`): 9 assets across all four categories (one in MAINTENANCE), 5 employees (`DEMO-E005` is inactive), and 7 check-outs. Three are open and overdue, one is open and not yet due, two were returned on time and one was returned late. It also creates the API user `demo` / `demo-password` and prints its token.

---

## 5. Project layout and design

```
config/                  settings (django-environ), urls, celery app + hourly beat schedule
apps/inventory/
  models.py              the four prescribed models, constraints, partial indexes
  services.py            every write: checkout_asset, return_checkout, create_asset
  selectors.py           every non-trivial read: asset holder annotation, employee_summary, overdue_checkouts
  exceptions.py          domain errors → HTTP status + machine-readable code
  serializers.py         input serializers validate shape; output serializers render
  views.py               thin: validate → call service/selector → serialize
  tasks.py               flag_overdue_checkouts
  management/commands/seed_demo_data.py
  tests/
```

Key decisions:

- **Concurrency (rule 7) is solved in the database.** `checkout_asset` runs in one `transaction.atomic()` and takes `SELECT … FOR UPDATE` locks on the **employee** row, then the **asset** row. The asset lock makes a concurrent second request wait, then re-read `CHECKED_OUT` and return 409. The employee lock serialises one employee's check-outs, so the three-check-out limit can't be raced by two requests for *different* assets. Locks are always taken in the same order (employee → asset), so check-outs can't deadlock each other. Returns lock check-out → asset and never touch employee rows, so they can't form a cycle with check-outs either.
- **Database-level guarantee:** a partial unique constraint `uniq_open_checkout_per_asset` on `(asset) WHERE returned_at IS NULL`. Even a code path that bypassed the service couldn't create two open check-outs for one asset. The service maps that `IntegrityError` to 409.
- **Atomicity (rule 5):** the check-out row and the asset status change are in the same transaction. There's a test that forces a failure after the insert and asserts nothing persisted.
- **Employee summary** is a single `annotate(Count(filter=…), Avg(returned_at - checked_out_at))` query on `Employee`. There's a test asserting exactly one query.
- **Overdue report** is one query: `select_related` plus `days_overdue` computed by Postgres, ordered by `due_at`. There's a test asserting the query count doesn't change with the row count.
- **Asset list** gets `current_holder` through correlated subqueries, not a lookup per row.
- **Celery task** idempotency is enforced by the `uniq_notice_per_checkout_per_day` constraint plus `bulk_create(ignore_conflicts=True)`, not by a racy "already sent?" check. It streams ids with `.iterator()` and inserts in batches.
- **Partial indexes** `(employee) WHERE open` and `(due_at) WHERE open` serve the limit check and the overdue report and task. They index only open check-outs, so they stay small as history grows.

---

## 6. Assumptions

1. **Authentication:** DRF Token auth. Any authenticated user can check an asset out *to* any employee. `Employee` isn't linked to Django's `User`, so there are no per-employee or role permissions.
2. **Overdue** means `returned_at IS NULL AND due_at < now`, strictly. An item due exactly now is not overdue.
3. **`days_overdue`** is the whole number of days elapsed since `due_at`, rounded down (1 hour late gives 0), computed in the database.
4. **`due_at`** must satisfy `now < due_at <= now + 30 days`, so exactly 30 days is allowed. Naive datetimes are treated as UTC (`TIME_ZONE = "UTC"`).
5. **Which error wins when several rules fail:** invalid `due_at` 400 → unknown employee 404 → inactive employee 400 → unknown asset 404 → asset not available 409 → limit reached 409. Missing or malformed fields give 400 from the serializer.
6. **Asset in MAINTENANCE** can't be checked out (rule 1 gives 409). Returning with `needs_maintenance: true` puts it in MAINTENANCE. Nothing in the API moves it back to AVAILABLE; that's done in the Django admin, since the spec defines no endpoint for it.
7. **Assets endpoints** are create, list and retrieve only; the spec lists no update or delete. A new asset can be created as AVAILABLE or MAINTENANCE, never CHECKED_OUT, which is only reachable through a check-out. `current_holder` is also included in list results, since the subquery makes it free.
8. **Employee summary:** `mean_hold_days` averages `returned_at - checked_out_at` over returned items only, rounded to 2 decimals, and is `null` if the employee has never returned anything. The response also includes `employee_code`, `full_name` and `is_active` for context.
9. **Overdue notices** are dated with the UTC date (`timezone.localdate()` with `TIME_ZONE="UTC"`). The task runs hourly, so at most one notice per check-out per UTC day.
10. **Seed idempotency:** assets and employees are upserted on their natural keys (`DEMO-*`). Check-outs have no natural key, so every check-out on a `DEMO-*` asset is deleted and recreated in the same transaction (their overdue notices cascade). Non-demo data is never touched. Demo check-outs are written through the ORM, not the service, because they need past `due_at` values that rule 4 rejects. `checked_out_at` is backdated with a queryset `update()`, because `auto_now_add` overwrites values passed to `create()`.
11. No fields were added to the four prescribed models. The only additions are the constraints and partial indexes described above.

## 7. Known gaps

- **Docker wasn't run on my machine:** my laptop doesn't support hardware virtualization. I developed and tested against a native PostgreSQL 16. The Dockerfile and compose stack are exercised by the `compose` job in GitHub Actions (build, migrate, seed, health, authenticated request, worker ping).
- **The browsable API has no CSS under Docker:** the image runs gunicorn with `DEBUG=False` and doesn't run `collectstatic` or serve static files (no whitenoise). The JSON API is unaffected.
- **The Celery worker and beat weren't exercised against a live Redis locally.** The task is tested by calling it directly (and via `.apply()` with no broker), and the hourly beat entry is asserted in `test_tasks.py`. The worker is checked to start and answer `inspect ping` in the CI compose job.
- **No email or notification delivery** for overdue notices; the spec only asks for the `OverdueNotice` record.
- **No rate limiting and no production settings split** (HTTPS, secure cookies, logging config).