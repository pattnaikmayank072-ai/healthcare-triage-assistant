import type {
  PatientData,
  OCRResult,
  QueueItem,
  ReferralNoteData,
  RiskLevel,
  FollowUpQuestion,
  SymptomEvent,
} from '@/types/triage';

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

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
  const complaint = (data.chiefComplaint || '').toLowerCase();
  const history = (data.symptomHistory || '').toLowerCase();

  const urgentKeywords = ['chest pain', 'breathing', 'unconscious', 'bleeding', 'seizure', 'severe', 'छाती', 'सांस', 'बेहोश', 'खून'];
  const priorityKeywords = ['fever', 'vomiting', 'diarrhea', 'pain', 'dizziness', 'बुखार', 'उल्टी', 'दर्द', 'चक्कर'];

  if (urgentKeywords.some((k) => complaint.includes(k) || history.includes(k))) score += 50;
  if (priorityKeywords.some((k) => complaint.includes(k) || history.includes(k))) score += 25;

  if (data.ocrResults?.length) {
    const flags = data.ocrResults.flatMap((r) => r.labValues.map((v) => v.flag));
    if (flags.includes('high') || flags.includes('low')) score += 15;
  }

  if (data.symptoms?.some((s) => s.severity === 'severe')) score += 20;
  if (data.symptoms?.some((s) => s.severity === 'moderate')) score += 10;

  score = Math.min(score, 100);

  let level: RiskLevel = 'green';
  if (score >= 70) level = 'red';
  else if (score >= 45) level = 'yellow';

  return { level, score };
}

export async function submitPatientData(
  data: Partial<PatientData>
): Promise<PatientData> {
  await delay(900);
  const { level, score } = calculateRisk(data);
  const patientData: PatientData = {
    patient: data.patient || {
      id: uid(),
      name: 'Unknown Patient',
      age: 0,
      gender: 'other',
      phone: '',
      village: '',
      facility: 'PHC - Default',
    },
    chiefComplaint: data.chiefComplaint || '',
    symptomHistory: data.symptomHistory || '',
    voiceTranscript: data.voiceTranscript || '',
    symptoms: data.symptoms || [],
    ocrResults: data.ocrResults || [],
    images: data.images || [],
    riskLevel: level,
    riskScore: score,
    followUpQuestions: mockFollowUps,
    status: 'pending',
    createdAt: nowISO(),
    updatedAt: nowISO(),
  };
  return patientData;
}

export async function processOCR(fileName: string): Promise<OCRResult> {
  await delay(1200);
  return {
    documentName: fileName,
    extractedAt: nowISO(),
    labValues: [...mockLabValues],
    rawText: `CBC Report\nPatient: Sample\nDate: ${new Date().toLocaleDateString()}\nHemoglobin: 8.2 g/dL (Low)\nWBC: 14.5 10^3/µL (High)\nPlatelets: 220 10^3/µL (Normal)\nBlood Sugar (Fasting): 168 mg/dL (High)\nBlood Pressure: 150/95 mmHg (High)`,
  };
}

export async function fetchQueue(): Promise<QueueItem[]> {
  await delay(600);
  return [
    {
      id: 'q1',
      patientName: 'Ramesh Kumar',
      patientId: 'p1',
      age: 54,
      gender: 'male',
      village: 'Sonbhadra',
      chiefComplaint: 'Chest pain and shortness of breath',
      riskLevel: 'red',
      riskScore: 82,
      status: 'pending',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
    {
      id: 'q2',
      patientName: 'Sunita Devi',
      patientId: 'p2',
      age: 32,
      gender: 'female',
      village: 'Chopan',
      chiefComplaint: 'High fever with body ache since 3 days',
      riskLevel: 'yellow',
      riskScore: 58,
      status: 'pending',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
    {
      id: 'q3',
      patientName: 'Asha Verma',
      patientId: 'p3',
      age: 28,
      gender: 'female',
      village: 'Obra',
      chiefComplaint: 'Routine antenatal checkup',
      riskLevel: 'green',
      riskScore: 22,
      status: 'approved',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
    {
      id: 'q4',
      patientName: 'Mohammad Yusuf',
      patientId: 'p4',
      age: 61,
      gender: 'male',
      village: 'Dudhi',
      chiefComplaint: 'Severe abdominal pain and vomiting',
      riskLevel: 'red',
      riskScore: 76,
      status: 'pending',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
    {
      id: 'q5',
      patientName: 'Priya Singh',
      patientId: 'p5',
      age: 19,
      gender: 'female',
      village: 'Renukoot',
      chiefComplaint: 'Mild headache and fatigue',
      riskLevel: 'green',
      riskScore: 28,
      status: 'approved',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
    {
      id: 'q6',
      patientName: 'Bablu Yadav',
      patientId: 'p6',
      age: 45,
      gender: 'male',
      village: 'Myorpur',
      chiefComplaint: 'Persistent cough with blood in sputum',
      riskLevel: 'yellow',
      riskScore: 52,
      status: 'overridden',
      createdAt: nowISO(),
      facility: 'PHC Sonbhadra',
    },
  ];
}

export async function updateTriageStatus(
  _id: string,
  _update: { status: PatientData['status']; overrideReason?: string; overriddenRiskLevel?: RiskLevel; clinicalNotes?: string }
): Promise<{ success: boolean }> {
  await delay(500);
  return { success: true };
}

export async function generateReferralNote(data: ReferralNoteData): Promise<string> {
  await delay(800);
  const dateStr = new Date(data.date).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
  return `REFERRAL NOTE
Government Health Facility - Tertiary Care Transfer
Date: ${dateStr}

From: ${data.fromFacility}
To: ${data.toFacility}

Patient Name: ${data.patientName}
Patient ID: ${data.patientId}
Age/Sex: ${data.age} / ${data.gender}

Reason for Referral: ${data.reason}

Clinical Summary:
${data.clinicalSummary}

Vitals & Lab Findings:
${data.vitals}

Triage Risk Level: ${data.riskLevel.toUpperCase()}

This referral is generated via the Multimodal Healthcare Triage Assistant for priority escalation. Please attend to this patient on an urgent basis.

Signature: _______________
Medical Officer, ${data.fromFacility}`;
}

export async function transcribeVoiceMock(durationMs: number): Promise<string> {
  await delay(durationMs);
  return 'Patient reports fever for the past three days with body ache and chills. No history of diabetes or hypertension. Has not taken any medication yet.';
}

export function createSymptomEvent(description: string, severity: SymptomEvent['severity']): SymptomEvent {
  return { id: uid(), timestamp: nowISO(), description, severity };
}
