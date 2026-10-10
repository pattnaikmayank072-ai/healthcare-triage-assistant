from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.response import Response

from .models import TriageCase
from .serializers import TriageCaseSerializer


class TriageCaseViewSet(viewsets.ModelViewSet):
    queryset = TriageCase.objects.all()
    serializer_class = TriageCaseSerializer
    lookup_field = 'id'

    def create(self, request, *args, **kwargs):
        payload = request.data.copy()
        now = timezone.now()
        # Always use Django's timezone-aware ISO value. This avoids rejecting
        # browser-generated timestamps while keeping the API contract simple.
        payload['created_at'] = now.isoformat()
        payload['updated_at'] = now.isoformat()
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        payload = request.data.copy()
        payload['updated_at'] = timezone.now()
        serializer = self.get_serializer(instance, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
