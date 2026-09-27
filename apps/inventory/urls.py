from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import AssetViewSet, CheckOutViewSet, EmployeeSummaryView, HealthView, OverdueReportView

router = DefaultRouter()
router.register("assets", AssetViewSet, basename="asset")
router.register("checkouts", CheckOutViewSet, basename="checkout")

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="auth-token"),
    path("health/", HealthView.as_view(), name="health"),
    path("employees/<str:employee_code>/summary/", EmployeeSummaryView.as_view(), name="employee-summary"),
    path("reports/overdue/", OverdueReportView.as_view(), name="overdue-report"),
    *router.urls,
]
