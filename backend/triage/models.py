from django.db import models


class TriageCase(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    patient_name = models.CharField(max_length=200)
    patient_age = models.PositiveIntegerField(default=0)
    patient_gender = models.CharField(max_length=20, default='other')
    patient_phone = models.CharField(max_length=40, blank=True, default='')
    village = models.CharField(max_length=200, blank=True, default='')
    facility = models.CharField(max_length=200, default='PHC Sonbhadra')
    chief_complaint = models.TextField(blank=True, default='')
    symptom_history = models.TextField(blank=True, default='')
    voice_transcript = models.TextField(blank=True, default='')
    symptoms = models.JSONField(default=list)
    ocr_results = models.JSONField(default=list)
    images = models.JSONField(default=list)
    risk_level = models.CharField(max_length=10, choices=[('red', 'Red'), ('yellow', 'Yellow'), ('green', 'Green')], default='green')
    risk_score = models.PositiveIntegerField(default=0)
    follow_up_questions = models.JSONField(default=list)
    status = models.CharField(max_length=20, default='pending')
    clinical_notes = models.TextField(blank=True, null=True)
    override_reason = models.TextField(blank=True, null=True)
    overridden_risk_level = models.CharField(max_length=10, blank=True, null=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']
