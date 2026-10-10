from django.contrib import admin
from .models import TriageCase


@admin.register(TriageCase)
class TriageCaseAdmin(admin.ModelAdmin):
    list_display = ('patient_name', 'risk_level', 'status', 'facility', 'created_at')
    list_filter = ('risk_level', 'status', 'facility')
    search_fields = ('patient_name', 'id', 'village')
