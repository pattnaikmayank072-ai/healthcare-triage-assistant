-- Triage Assistant hosted database
create extension if not exists pgcrypto;

create table if not exists public.triage_cases (
  id text primary key,
  patient_name text not null,
  patient_age integer not null default 0,
  patient_gender text not null default 'other',
  patient_phone text not null default '',
  village text not null default '',
  facility text not null default 'PHC Sonbhadra',
  chief_complaint text not null default '',
  symptom_history text not null default '',
  voice_transcript text not null default '',
  symptoms jsonb not null default '[]'::jsonb,
  ocr_results jsonb not null default '[]'::jsonb,
  images jsonb not null default '[]'::jsonb,
  risk_level text not null check (risk_level in ('red', 'yellow', 'green')),
  risk_score integer not null default 0 check (risk_score between 0 and 100),
  follow_up_questions jsonb not null default '[]'::jsonb,
  status text not null default 'pending' check (status in ('pending', 'approved', 'overridden', 'referred')),
  clinical_notes text,
  override_reason text,
  overridden_risk_level text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists triage_cases_status_idx on public.triage_cases(status);
create index if not exists triage_cases_created_at_idx on public.triage_cases(created_at desc);

alter table public.triage_cases enable row level security;
-- Hackathon demo policy: the app uses the public anon key and has no patient login yet.
-- Replace with authenticated-user policies before handling real PHI.
drop policy if exists "demo read triage cases" on public.triage_cases;
drop policy if exists "demo insert triage cases" on public.triage_cases;
drop policy if exists "demo update triage cases" on public.triage_cases;
create policy "demo read triage cases" on public.triage_cases for select using (true);
create policy "demo insert triage cases" on public.triage_cases for insert with check (true);
create policy "demo update triage cases" on public.triage_cases for update using (true) with check (true);
