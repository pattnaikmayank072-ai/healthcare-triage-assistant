from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='TriageCase',
            fields=[
                ('id', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('patient_name', models.CharField(max_length=200)),
                ('patient_age', models.PositiveIntegerField(default=0)),
                ('patient_gender', models.CharField(default='other', max_length=20)),
                ('patient_phone', models.CharField(blank=True, default='', max_length=40)),
                ('village', models.CharField(blank=True, default='', max_length=200)),
                ('facility', models.CharField(default='PHC Sonbhadra', max_length=200)),
                ('chief_complaint', models.TextField(blank=True, default='')),
                ('symptom_history', models.TextField(blank=True, default='')),
                ('voice_transcript', models.TextField(blank=True, default='')),
                ('symptoms', models.JSONField(default=list)),
                ('ocr_results', models.JSONField(default=list)),
                ('images', models.JSONField(default=list)),
                ('risk_level', models.CharField(choices=[('red', 'Red'), ('yellow', 'Yellow'), ('green', 'Green')], default='green', max_length=10)),
                ('risk_score', models.PositiveIntegerField(default=0)),
                ('follow_up_questions', models.JSONField(default=list)),
                ('status', models.CharField(default='pending', max_length=20)),
                ('clinical_notes', models.TextField(blank=True, null=True)),
                ('override_reason', models.TextField(blank=True, null=True)),
                ('overridden_risk_level', models.CharField(blank=True, max_length=10, null=True)),
                ('created_at', models.DateTimeField()),
                ('updated_at', models.DateTimeField()),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
