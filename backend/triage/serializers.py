from rest_framework import serializers
from .models import TriageCase


class TriageCaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = TriageCase
        fields = '__all__'
