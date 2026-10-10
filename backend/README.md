# Minimal Django backend

This service provides a small REST API for triage cases. It uses SQLite by default for a fast local demo and accepts a PostgreSQL `DATABASE_URL` for deployment.

## Run locally

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8000
```

API endpoints:

- `GET /api/triage-cases/`
- `POST /api/triage-cases/`
- `GET /api/triage-cases/<id>/`
- `PATCH /api/triage-cases/<id>/`

Environment variables:

```text
DJANGO_SECRET_KEY=change-this
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=your-backend-host.com
DATABASE_URL=postgresql://...
CORS_ALLOW_ALL_ORIGINS=true
```

This is a hackathon prototype API. Add authentication, facility scoping, audit logging, rate limits, and privacy review before handling real patient data.
