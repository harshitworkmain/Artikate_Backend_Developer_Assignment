from rest_framework import serializers

from .models import CheckOut


class CheckOutInputSerializer(serializers.Serializer):
    asset_tag = serializers.CharField(max_length=32)
    employee_code = serializers.CharField(max_length=16)
    due_at = serializers.DateTimeField()


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
