from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors, services
from .filters import AssetFilter
from .serializers import (
    AssetInputSerializer,
    AssetOutputSerializer,
    CheckOutInputSerializer,
    CheckOutOutputSerializer,
    EmployeeSummaryOutputSerializer,
    OverdueCheckoutOutputSerializer,
    ReturnInputSerializer,
)


class CheckOutViewSet(viewsets.ViewSet):
    lookup_value_regex = r"\d+"

    def create(self, request):
        data = CheckOutInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        checkout = services.checkout_asset(**data.validated_data)
        return Response(CheckOutOutputSerializer(checkout).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="return")
    def return_(self, request, pk=None):
        data = ReturnInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        checkout = services.return_checkout(checkout_id=int(pk), **data.validated_data)
        return Response(CheckOutOutputSerializer(checkout).data)


class AssetViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = AssetOutputSerializer
    filterset_class = AssetFilter
    search_fields = ["name", "asset_tag"]
    ordering_fields = ["asset_tag", "name", "created_at"]
    ordering = ["asset_tag"]

    def get_queryset(self):
        return selectors.asset_queryset()

    def create(self, request, *args, **kwargs):
        data = AssetInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        asset = services.create_asset(**data.validated_data)
        return Response(AssetOutputSerializer(asset).data, status=status.HTTP_201_CREATED)


class EmployeeSummaryView(APIView):
    def get(self, request, employee_code: str):
        summary = selectors.employee_summary(employee_code=employee_code)
        return Response(EmployeeSummaryOutputSerializer(summary).data)


class OverdueReportView(generics.ListAPIView):
    serializer_class = OverdueCheckoutOutputSerializer
    # Fixed most-overdue-first order; no client filtering/ordering on this report.
    filter_backends = []

    def get_queryset(self):
        return selectors.overdue_checkouts()


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if selectors.database_is_healthy():
            return Response({"status": "ok", "database": "ok"})
        return Response(
            {"status": "degraded", "database": "error"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
