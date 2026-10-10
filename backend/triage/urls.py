from rest_framework.routers import DefaultRouter
from .views import TriageCaseViewSet

router = DefaultRouter()
router.register('triage-cases', TriageCaseViewSet, basename='triage-case')

urlpatterns = router.urls
