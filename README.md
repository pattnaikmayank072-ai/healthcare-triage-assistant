# Triage Assistant

A clinician-led triage workspace for primary health centres: patient intake, voice/OCR demo flows, risk scoring, doctor review, queue management, referral notes, and analytics.

## What is connected now

- **Durable case persistence:** new patient submissions and queue changes are saved.
- **Hosted database support:** configure Supabase with `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` and run [`supabase/schema.sql`](./supabase/schema.sql).
- **Demo fallback:** without Supabase variables, the same flows work using browser `localStorage`, so a hackathon demo does not fail because a cloud database is not configured.
- **Queue actions persist:** approve, override, and refer actions survive refreshes.
- **Existing Python backend:** the `triage-assistant-phase6a/` folder is retained as a separate advanced backend draft. The live Vite prototype uses the browser/Supabase adapter for a reliable few-hours-before-demo path.

## Run locally

```bash
npm install
npm run dev
```

Validate before presenting:

```bash
npm run typecheck
npm run lint
npm run build
```

## Connect Supabase + Vercel

1. Create a Supabase project.
2. Open SQL Editor and run [`supabase/schema.sql`](./supabase/schema.sql).
3. In Vercel project settings, add `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` for Preview and Production.
4. Redeploy. The app automatically uses Supabase when both values exist.

The included SQL policies are intentionally permissive for a synthetic hackathon demo. **Do not use them with real patient data** without adding authenticated roles, facility scoping, audit logging, and a privacy/compliance review.
