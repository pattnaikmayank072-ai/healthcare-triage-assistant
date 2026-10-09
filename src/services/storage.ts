import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { PatientData, QueueItem } from '@/types/triage';

const QUEUE_KEY = 'triage-assistant:queue:v1';
const PATIENTS_KEY = 'triage-assistant:patients:v1';
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const isCloudDatabaseConfigured = Boolean(supabaseUrl && supabaseAnonKey);
export const supabase: SupabaseClient | null = isCloudDatabaseConfigured
  ? createClient(supabaseUrl!, supabaseAnonKey!, { auth: { persistSession: true } })
  : null;

const read = <T>(key: string, fallback: T): T => {
  try {
    const value = localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
};

const write = (key: string, value: unknown) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // The UI remains usable if browser storage is disabled or full.
  }
};

export function readLocalQueue(): QueueItem[] {
  return read<QueueItem[]>(QUEUE_KEY, []);
}

export function writeLocalQueue(items: QueueItem[]) {
  write(QUEUE_KEY, items);
}

export function readLocalPatients(): PatientData[] {
  return read<PatientData[]>(PATIENTS_KEY, []);
}

export function writeLocalPatients(items: PatientData[]) {
  write(PATIENTS_KEY, items);
}

export async function savePatientToDatabase(patient: PatientData): Promise<void> {
  const patients = readLocalPatients().filter((item) => item.patient.id !== patient.patient.id);
  writeLocalPatients([patient, ...patients]);

  if (!supabase) return;
  try {
    await supabase.from('triage_cases').upsert({
      id: patient.patient.id,
      patient_name: patient.patient.name,
      patient_age: patient.patient.age,
      patient_gender: patient.patient.gender,
      patient_phone: patient.patient.phone,
      village: patient.patient.village,
      facility: patient.patient.facility,
      chief_complaint: patient.chiefComplaint,
      symptom_history: patient.symptomHistory,
      voice_transcript: patient.voiceTranscript,
      symptoms: patient.symptoms,
      ocr_results: patient.ocrResults,
      images: patient.images,
      risk_level: patient.riskLevel,
      risk_score: patient.riskScore,
      follow_up_questions: patient.followUpQuestions,
      status: patient.status,
      clinical_notes: patient.clinicalNotes ?? null,
      override_reason: patient.overrideReason ?? null,
      overridden_risk_level: patient.overriddenRiskLevel ?? null,
      created_at: patient.createdAt,
      updated_at: patient.updatedAt,
    });
  } catch {
    // Keep the offline-first demo usable while the browser retries cloud sync later.
  }
}

export async function readCloudQueue(): Promise<QueueItem[] | null> {
  if (!supabase) return null;
  try {
    const { data, error } = await supabase
      .from('triage_cases')
      .select('id,patient_name,patient_age,patient_gender,village,chief_complaint,risk_level,risk_score,status,created_at,facility')
      .order('created_at', { ascending: false });
    if (error) return null;
    return (data ?? []).map((row) => ({
      id: row.id,
      patientName: row.patient_name,
      patientId: row.id,
      age: row.patient_age,
      gender: row.patient_gender,
      village: row.village,
      chiefComplaint: row.chief_complaint,
      riskLevel: row.risk_level,
      riskScore: row.risk_score,
      status: row.status,
      createdAt: row.created_at,
      facility: row.facility,
    }));
  } catch {
    return null;
  }
}

export async function updatePatientInDatabase(
  id: string,
  update: Partial<Pick<PatientData, 'status' | 'overrideReason' | 'overriddenRiskLevel' | 'clinicalNotes'>>,
): Promise<void> {
  const patients = readLocalPatients().map((patient) =>
    patient.patient.id === id ? { ...patient, ...update, updatedAt: new Date().toISOString() } : patient,
  );
  writeLocalPatients(patients);

  const queue = readLocalQueue().map((item) =>
    item.id === id
      ? { ...item, ...(update.status ? { status: update.status } : {}), ...(update.overriddenRiskLevel ? { riskLevel: update.overriddenRiskLevel } : {}) }
      : item,
  );
  writeLocalQueue(queue);

  if (!supabase) return;
  try {
    await supabase.from('triage_cases').update({
      ...(update.status ? { status: update.status } : {}),
      ...(update.overrideReason ? { override_reason: update.overrideReason } : {}),
      ...(update.overriddenRiskLevel ? { overridden_risk_level: update.overriddenRiskLevel, risk_level: update.overriddenRiskLevel } : {}),
      ...(update.clinicalNotes ? { clinical_notes: update.clinicalNotes } : {}),
      updated_at: new Date().toISOString(),
    }).eq('id', id);
  } catch {
    // Local queue state is already updated above and remains available offline.
  }
}
