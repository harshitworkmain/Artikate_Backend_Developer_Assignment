from rest_framework.routers import DefaultRouter

from .views import CheckOutViewSet

router = DefaultRouter()
router.register("checkouts", CheckOutViewSet, basename="checkout")

urlpatterns = router.urls
