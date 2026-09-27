from django.contrib import admin

from .models import Asset, CheckOut, Employee, OverdueNotice


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "name", "category", "status", "purchase_date")
    list_filter = ("category", "status")
    search_fields = ("asset_tag", "name")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "full_name", "email", "is_active")
    list_filter = ("is_active",)
    search_fields = ("employee_code", "full_name", "email")


@admin.register(CheckOut)
class CheckOutAdmin(admin.ModelAdmin):
    list_display = ("asset", "employee", "checked_out_at", "due_at", "returned_at")
    list_filter = ("asset__category", "returned_at")
    search_fields = ("asset__asset_tag", "employee__employee_code", "employee__full_name")
    list_select_related = ("asset", "employee")


@admin.register(OverdueNotice)
class OverdueNoticeAdmin(admin.ModelAdmin):
    list_display = ("checkout", "notice_date", "created_at")
    list_filter = ("notice_date",)
    search_fields = ("checkout__asset__asset_tag", "checkout__employee__employee_code")
    list_select_related = ("checkout__asset", "checkout__employee")
