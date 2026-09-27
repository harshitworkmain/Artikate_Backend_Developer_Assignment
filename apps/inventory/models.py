from django.db import models
from django.db.models import Q


class Asset(models.Model):
    class Category(models.TextChoices):
        CAMERA = "CAMERA", "Camera"
        LAPTOP = "LAPTOP", "Laptop"
        SENSOR = "SENSOR", "Sensor"
        VEHICLE = "VEHICLE", "Vehicle"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        CHECKED_OUT = "CHECKED_OUT", "Checked out"
        MAINTENANCE = "MAINTENANCE", "Maintenance"

    asset_tag = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=16, choices=Category.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)
    purchase_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.asset_tag} - {self.name}"


class Employee(models.Model):
    employee_code = models.CharField(max_length=16, unique=True, db_index=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.employee_code} - {self.full_name}"


class CheckOut(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="checkouts")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="checkouts")
    checked_out_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    condition_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-checked_out_at"]
        constraints = [
            # DB-level backstop: an asset can never have two open check-outs.
            models.UniqueConstraint(
                fields=["asset"],
                condition=Q(returned_at__isnull=True),
                name="uniq_open_checkout_per_asset",
            ),
        ]
        indexes = [
            # Serves the 3-open-check-outs limit.
            models.Index(
                fields=["employee"],
                condition=Q(returned_at__isnull=True),
                name="idx_open_co_by_employee",
            ),
            # Serves the overdue report and Celery task.
            models.Index(
                fields=["due_at"],
                condition=Q(returned_at__isnull=True),
                name="idx_open_co_due_at",
            ),
        ]

    def __str__(self):
        return f"{self.asset} -> {self.employee}"


class OverdueNotice(models.Model):
    checkout = models.ForeignKey(CheckOut, on_delete=models.CASCADE, related_name="notices")
    notice_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # At most one overdue notice per check-out per day.
            models.UniqueConstraint(
                fields=["checkout", "notice_date"],
                name="uniq_notice_per_checkout_per_day",
            ),
        ]

    def __str__(self):
        return f"Notice {self.notice_date} for checkout {self.checkout_id}"
