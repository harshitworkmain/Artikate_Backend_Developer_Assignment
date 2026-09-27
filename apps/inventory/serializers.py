from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .models import Asset, CheckOut


class CheckOutInputSerializer(serializers.Serializer):
    asset_tag = serializers.CharField(max_length=32)
    employee_code = serializers.CharField(max_length=16)
    due_at = serializers.DateTimeField()


class ReturnInputSerializer(serializers.Serializer):
    condition_note = serializers.CharField(required=False, allow_blank=True, default="")
    needs_maintenance = serializers.BooleanField(required=False, default=False)


class CheckOutOutputSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag")
    asset_name = serializers.CharField(source="asset.name")
    employee_code = serializers.CharField(source="employee.employee_code")
    employee_name = serializers.CharField(source="employee.full_name")

    class Meta:
        model = CheckOut
        fields = [
            "id",
            "asset_tag",
            "asset_name",
            "employee_code",
            "employee_name",
            "checked_out_at",
            "due_at",
            "returned_at",
            "condition_note",
        ]
        read_only_fields = fields


class EmployeeSummaryOutputSerializer(serializers.Serializer):
    employee_code = serializers.CharField()
    full_name = serializers.CharField()
    is_active = serializers.BooleanField()
    lifetime_checkouts = serializers.IntegerField()
    currently_held = serializers.IntegerField()
    currently_overdue = serializers.IntegerField()
    mean_hold_days = serializers.FloatField(allow_null=True)


class OverdueCheckoutOutputSerializer(serializers.Serializer):
    checkout_id = serializers.IntegerField(source="id")
    asset_name = serializers.CharField(source="asset.name")
    asset_tag = serializers.CharField(source="asset.asset_tag")
    employee_code = serializers.CharField(source="employee.employee_code")
    employee_name = serializers.CharField(source="employee.full_name")
    due_at = serializers.DateTimeField()
    days_overdue = serializers.IntegerField()


class AssetInputSerializer(serializers.Serializer):
    asset_tag = serializers.CharField(
        max_length=32,
        validators=[UniqueValidator(queryset=Asset.objects.all(), message="asset with this asset tag already exists.")],
    )
    name = serializers.CharField(max_length=120)
    category = serializers.ChoiceField(choices=Asset.Category.choices)
    purchase_date = serializers.DateField()
    # CHECKED_OUT is only reachable through a check-out, never set directly.
    status = serializers.ChoiceField(
        choices=[Asset.Status.AVAILABLE, Asset.Status.MAINTENANCE],
        required=False,
        default=Asset.Status.AVAILABLE,
    )


class AssetOutputSerializer(serializers.ModelSerializer):
    current_holder = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_tag",
            "name",
            "category",
            "status",
            "purchase_date",
            "current_holder",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_current_holder(self, obj: Asset) -> dict | None:
        code = getattr(obj, "holder_code", None)
        if code is None:
            return None
        return {"employee_code": code, "full_name": obj.holder_name}
