from rest_framework import status, viewsets
from rest_framework.response import Response

from . import services
from .serializers import CheckOutInputSerializer, CheckOutOutputSerializer


class CheckOutViewSet(viewsets.ViewSet):
    def create(self, request):
        data = CheckOutInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        checkout = services.checkout_asset(**data.validated_data)
        return Response(CheckOutOutputSerializer(checkout).data, status=status.HTTP_201_CREATED)
