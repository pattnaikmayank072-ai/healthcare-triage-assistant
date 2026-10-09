import type {
  PatientData,
  OCRResult,
  QueueItem,
  ReferralNoteData,
  RiskLevel,
  FollowUpQuestion,
  SymptomEvent,
} from '@/types/triage';
import {
  readCloudQueue,
  readLocalQueue,
  writeLocalQueue,
  savePatientToDatabase,
  updatePatientInDatabase,
} from '@/services/storage';

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const uid = () => Math.random().toString(36).slice(2, 10);
const nowISO = () => new Date().toISOString();

const mockLabValues = [
  { key: 'hemoglobin', label: 'Hemoglobin', labelHi: 'हीमोग्लोबिन', value: '8.2', unit: 'g/dL', refRange: '12-16', flag: 'low' as const },
  { key: 'wbc', label: 'WBC', labelHi: 'डब्ल्यूबीसी', value: '14.5', unit: '10^3/µL', refRange: '4-11', flag: 'high' as const },
  { key: 'platelets', label: 'Platelets', labelHi: 'प्लेटलेट्स', value: '220', unit: '10^3/µL', refRange: '150-450', flag: 'normal' as const },
  { key: 'bloodSugar', label: 'Blood Sugar (Fasting)', labelHi: 'ब्लड शुगर (उपवास)', value: '168', unit: 'mg/dL', refRange: '70-100', flag: 'high' as const },
  { key: 'bp', label: 'Blood Pressure', labelHi: 'ब्लड प्रेशर', value: '150/95', unit: 'mmHg', refRange: '120/80', flag: 'high' as const },
];

const mockFollowUps: FollowUpQuestion[] = [
  { id: uid(), question: 'How long has the patient experienced these symptoms?', questionHi: 'रोगी को ये लक्षण कब से हैं?' },
  { id: uid(), question: 'Is there any history of chronic illness (diabetes, hypertension)?', questionHi: 'क्या कोई पुरानी बीमारी (मधुमेह, उच्च रक्तचाप) है?' },
  { id: uid(), question: 'Has the patient taken any medication in the last 24 hours?', questionHi: 'क्या रोगी ने पिछले 24 घंटे में कोई दवा ली है?' },
  { id: uid(), question: 'Any recent travel or exposure to infectious disease?', questionHi: 'कोई हाल की यात्रा या संक्रामक रोग संपर्क?' },
];

function calculateRisk(data: Partial<PatientData>): { level: RiskLevel; score: number } {
  let score = 20;
  const text = `${data.chiefComplaint || ''} ${data.symptomHistory || ''}`.toLowerCase();
  const urgent = ['chest pain', 'breathing', 'unconscious', 'bleeding', 'seizure', 'severe', 'छाती', 'सांस', 'बेहोश', 'खून'];
  const priority = ['fever', 'vomiting', 'diarrhea', 'pain', 'dizziness', 'बुखार', 'उल्टी', 'दर्द', 'चक्कर'];
  if (urgent.some((keyword) => text.includes(keyword))) score += 50;
  if (priority.some((keyword) => text.includes(keyword))) score += 25;
  if (data.ocrResults?.some((result) => result.labValues.some((value) => value.flag !== 'normal'))) score += 15;
  if (data.symptoms?.some((symptom) => symptom.severity === 'severe')) score += 20;
  if (data.symptoms?.some((symptom) => symptom.severity === 'moderate')) score += 10;
  score = Math.min(score, 100);
  return { level: score >= 70 ? 'red' : score >= 45 ? 'yellow' : 'green', score };
}

const seedQueue = (): QueueItem[] => [
  ['q1', 'Ramesh Kumar', 54, 'Sonbhadra', 'Chest pain and shortness of breath', 'red', 82, 'pending'],
  ['q2', 'Sunita Devi', 32, 'Chopan', 'High fever with body ache since 3 days', 'yellow', 58, 'pending'],
  ['q3', 'Asha Verma', 28, 'Obra', 'Routine antenatal checkup', 'green', 22, 'approved'],
  ['q4', 'Mohammad Yusuf', 61, 'Dudhi', 'Severe abdominal pain and vomiting', 'red', 76, 'pending'],
  ['q5', 'Priya Singh', 19, 'Renukoot', 'Mild headache and fatigue', 'green', 28, 'approved'],
  ['q6', 'Bablu Yadav', 45, 'Myorpur', 'Persistent cough with blood in sputum', 'yellow', 52, 'overridden'],
].map(([id, patientName, age, village, chiefComplaint, riskLevel, riskScore, status]) => ({
  id: id as string, patientName: patientName as string, patientId: id as string, age: age as number, gender: 'other', village: village as string,
  chiefComplaint: chiefComplaint as string, riskLevel: riskLevel as RiskLevel, riskScore: riskScore as number,
  status: status as PatientData['status'], createdAt: nowISO(), facility: 'PHC Sonbhadra',
}));

export async function submitPatientData(data: Partial<PatientData>): Promise<PatientData> {
  await delay(500);
  const { level, score } = calculateRisk(data);
  const patientData: PatientData = {
    patient: data.patient || { id: uid(), name: 'Unknown Patient', age: 0, gender: 'other', phone: '', village: '', facility: 'PHC - Default' },
    chiefComplaint: data.chiefComplaint || '', symptomHistory: data.symptomHistory || '', voiceTranscript: data.voiceTranscript || '',
    symptoms: data.symptoms || [], ocrResults: data.ocrResults || [], images: data.images || [], riskLevel: level, riskScore: score,
    followUpQuestions: mockFollowUps, status: 'pending', createdAt: nowISO(), updatedAt: nowISO(),
  };
  await savePatientToDatabase(patientData);
  const item: QueueItem = { id: patientData.patient.id, patientName: patientData.patient.name, patientId: patientData.patient.id, age: patientData.patient.age, gender: patientData.patient.gender, village: patientData.patient.village, chiefComplaint: patientData.chiefComplaint, riskLevel: patientData.riskLevel, riskScore: patientData.riskScore, status: patientData.status, createdAt: patientData.createdAt, facility: patientData.patient.facility };
  writeLocalQueue([item, ...readLocalQueue().filter((existing) => existing.id !== item.id)]);
  return patientData;
}

export async function processOCR(fileName: string): Promise<OCRResult> {
  await delay(800);
  return { documentName: fileName, extractedAt: nowISO(), labValues: [...mockLabValues], rawText: `CBC Report\nPatient: Sample\nDate: ${new Date().toLocaleDateString()}\nHemoglobin: 8.2 g/dL (Low)\nWBC: 14.5 10^3/µL (High)\nPlatelets: 220 10^3/µL (Normal)\nBlood Sugar (Fasting): 168 mg/dL (High)\nBlood Pressure: 150/95 mmHg (High)` };
}

export async function fetchQueue(): Promise<QueueItem[]> {
  await delay(250);
  const cloudQueue = await readCloudQueue();
  if (cloudQueue) {
    writeLocalQueue(cloudQueue);
    return cloudQueue;
  }
  const existing = readLocalQueue();
  if (existing.length) return existing;
  const seeded = seedQueue();
  writeLocalQueue(seeded);
  return seeded;
}

export async function updateTriageStatus(id: string, update: { status: PatientData['status']; overrideReason?: string; overriddenRiskLevel?: RiskLevel; clinicalNotes?: string }): Promise<{ success: boolean }> {
  await delay(250);
  await updatePatientInDatabase(id, update);
  const queue = readLocalQueue().map((item) => item.id === id ? { ...item, status: update.status, ...(update.overriddenRiskLevel ? { riskLevel: update.overriddenRiskLevel } : {}) } : item);
  writeLocalQueue(queue);
  return { success: true };
}

export async function generateReferralNote(data: ReferralNoteData): Promise<string> {
  await delay(500);
  const dateStr = new Date(data.date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  return `REFERRAL NOTE\nGovernment Health Facility - Tertiary Care Transfer\nDate: ${dateStr}\n\nFrom: ${data.fromFacility}\nTo: ${data.toFacility}\n\nPatient Name: ${data.patientName}\nPatient ID: ${data.patientId}\nAge/Sex: ${data.age} / ${data.gender}\n\nReason for Referral: ${data.reason}\n\nClinical Summary:\n${data.clinicalSummary}\n\nVitals & Lab Findings:\n${data.vitals}\n\nTriage Risk Level: ${data.riskLevel.toUpperCase()}\n\nThis referral is generated via the Multimodal Healthcare Triage Assistant for priority escalation. Please attend to this patient on an urgent basis.\n\nSignature: _______________\nMedical Officer, ${data.fromFacility}`;
}

export async function transcribeVoiceMock(durationMs: number): Promise<string> {
  await delay(durationMs);
  return 'Patient reports fever for the past three days with body ache and chills. No history of diabetes or hypertension. Has not taken any medication yet.';
}

export function createSymptomEvent(description: string, severity: SymptomEvent['severity']): SymptomEvent {
  return { id: uid(), timestamp: nowISO(), description, severity };
}
