from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.inventory.models import Asset, CheckOut, Employee

DEMO_PREFIX = "DEMO-"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-password"

# (asset_tag, name, category, resting status when not checked out, purchase_date)
ASSETS = [
    ("DEMO-CAM-01", "Canon EOS R6", Asset.Category.CAMERA, Asset.Status.AVAILABLE, date(2023, 3, 14)),
    ("DEMO-CAM-02", "Sony A7 IV", Asset.Category.CAMERA, Asset.Status.AVAILABLE, date(2023, 6, 2)),
    ("DEMO-LAP-01", "ThinkPad X1 Carbon", Asset.Category.LAPTOP, Asset.Status.AVAILABLE, date(2024, 1, 9)),
    ("DEMO-LAP-02", "MacBook Pro 14", Asset.Category.LAPTOP, Asset.Status.AVAILABLE, date(2024, 2, 20)),
    ("DEMO-LAP-03", "Dell Latitude 7440", Asset.Category.LAPTOP, Asset.Status.AVAILABLE, date(2024, 5, 11)),
    ("DEMO-SEN-01", "Air Quality Sensor", Asset.Category.SENSOR, Asset.Status.AVAILABLE, date(2022, 11, 30)),
    ("DEMO-SEN-02", "Vibration Sensor", Asset.Category.SENSOR, Asset.Status.MAINTENANCE, date(2022, 8, 17)),
    ("DEMO-VEH-01", "Ford Transit Van", Asset.Category.VEHICLE, Asset.Status.AVAILABLE, date(2021, 4, 5)),
    ("DEMO-VEH-02", "Toyota Hilux", Asset.Category.VEHICLE, Asset.Status.AVAILABLE, date(2021, 9, 23)),
]

# (employee_code, full_name, email, is_active)
EMPLOYEES = [
    ("DEMO-E001", "Asha Rao", "asha.rao@demo.example.com", True),
    ("DEMO-E002", "Ben Okafor", "ben.okafor@demo.example.com", True),
    ("DEMO-E003", "Chen Wei", "chen.wei@demo.example.com", True),
    ("DEMO-E004", "Dana Kowalski", "dana.kowalski@demo.example.com", True),
    ("DEMO-E005", "Eli Novak", "eli.novak@demo.example.com", False),
]

# (asset_tag, employee_code, checked_out offset, due offset, returned offset or None),
# offsets relative to now.
CHECKOUTS = [
    # open, overdue
    ("DEMO-CAM-01", "DEMO-E001", timedelta(days=-10), timedelta(days=-3), None),
    ("DEMO-LAP-01", "DEMO-E002", timedelta(days=-5), timedelta(days=-1), None),
    ("DEMO-VEH-01", "DEMO-E001", timedelta(days=-2), timedelta(hours=-6), None),
    # open, not yet due
    ("DEMO-LAP-02", "DEMO-E003", timedelta(days=-1), timedelta(days=6), None),
    # returned on time
    ("DEMO-CAM-02", "DEMO-E002", timedelta(days=-20), timedelta(days=-13), timedelta(days=-14)),
    ("DEMO-SEN-01", "DEMO-E003", timedelta(days=-30), timedelta(days=-25), timedelta(days=-26)),
    # returned late
    ("DEMO-VEH-02", "DEMO-E004", timedelta(days=-15), timedelta(days=-10), timedelta(days=-8)),
]


class Command(BaseCommand):
    help = (
        "Load idempotent demo data. Assets and employees are upserted on their natural keys "
        "(DEMO-* asset_tag / employee_code). Check-outs have no natural key, so every check-out "
        "on a DEMO-* asset is deleted (overdue notices cascade) and recreated in the same "
        "transaction; asset statuses are then derived from the open check-outs. Also ensures a "
        f"non-staff API user '{DEMO_USERNAME}' with a token and prints the token."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        open_tags = {tag for tag, _, _, _, returned in CHECKOUTS if returned is None}

        deleted, _ = CheckOut.objects.filter(asset__asset_tag__startswith=DEMO_PREFIX).delete()

        assets = {}
        for tag, name, category, resting_status, purchased in ASSETS:
            assets[tag], _ = Asset.objects.update_or_create(
                asset_tag=tag,
                defaults={
                    "name": name,
                    "category": category,
                    "status": Asset.Status.CHECKED_OUT if tag in open_tags else resting_status,
                    "purchase_date": purchased,
                },
            )

        employees = {}
        for code, full_name, email, is_active in EMPLOYEES:
            employees[code], _ = Employee.objects.update_or_create(
                employee_code=code,
                defaults={"full_name": full_name, "email": email, "is_active": is_active},
            )

        for tag, code, out_offset, due_offset, returned_offset in CHECKOUTS:
            # Written via the ORM, not services.checkout_asset: demo history needs past
            # due_at values, which the service's due-date rule rejects by design.
            checkout = CheckOut.objects.create(
                asset=assets[tag],
                employee=employees[code],
                due_at=now + due_offset,
                returned_at=now + returned_offset if returned_offset is not None else None,
            )
            # auto_now_add always stamps "now" on create, so backdate with a queryset update.
            CheckOut.objects.filter(pk=checkout.pk).update(checked_out_at=now + out_offset)

        user, _ = get_user_model().objects.get_or_create(username=DEMO_USERNAME)
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password"])
        token, _ = Token.objects.get_or_create(user=user)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(ASSETS)} assets, {len(EMPLOYEES)} employees, "
                f"{len(CHECKOUTS)} check-outs (replaced {deleted} demo rows)."
            )
        )
        self.stdout.write(f"API user: {DEMO_USERNAME} / {DEMO_PASSWORD}")
        self.stdout.write(f"API token: {token.key}")
