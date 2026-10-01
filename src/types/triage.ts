export type Role = 'healthWorker' | 'doctor';

export type Language = 'en' | 'hi';

export type RiskLevel = 'red' | 'yellow' | 'green';

export type TriageStatus = 'pending' | 'approved' | 'overridden' | 'referred';

export type View = 'intake' | 'review' | 'queue' | 'referral' | 'analytics';

export interface LabValue {
  key: string;
  label: string;
  labelHi: string;
  value: string;
  unit: string;
  refRange: string;
  flag: 'high' | 'low' | 'normal';
}

export interface OCRResult {
  documentName: string;
  extractedAt: string;
  labValues: LabValue[];
  rawText: string;
}

export interface SymptomEvent {
  id: string;
  timestamp: string;
  description: string;
  severity: 'mild' | 'moderate' | 'severe';
}

export interface UploadedImage {
  id: string;
  name: string;
  previewUrl: string;
  caption: string;
}

export interface FollowUpQuestion {
  id: string;
  question: string;
  questionHi: string;
}

export interface Patient {
  id: string;
  name: string;
  age: number;
  gender: 'male' | 'female' | 'other';
  phone: string;
  village: string;
  facility: string;
}

export interface PatientData {
  patient: Patient;
  chiefComplaint: string;
  symptomHistory: string;
  voiceTranscript: string;
  symptoms: SymptomEvent[];
  ocrResults: OCRResult[];
  images: UploadedImage[];
  riskLevel: RiskLevel;
  riskScore: number;
  followUpQuestions: FollowUpQuestion[];
  status: TriageStatus;
  createdAt: string;
  updatedAt: string;
  overrideReason?: string;
  overriddenRiskLevel?: RiskLevel;
  clinicalNotes?: string;
  referralNote?: string;
}

export interface QueueItem {
  id: string;
  patientName: string;
  patientId: string;
  age: number;
  gender: string;
  village: string;
  chiefComplaint: string;
  riskLevel: RiskLevel;
  riskScore: number;
  status: TriageStatus;
  createdAt: string;
  facility: string;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  actor: string;
  actorRole: Role;
  action: string;
  detail: string;
}

export interface ReferralNoteData {
  patientName: string;
  patientId: string;
  age: number;
  gender: string;
  fromFacility: string;
  toFacility: string;
  reason: string;
  clinicalSummary: string;
  vitals: string;
  riskLevel: RiskLevel;
  date: string;
}

export interface UserSession {
  role: Role;
  name: string;
  facility: string;
  identifier: string;
  sigVerified: boolean;
  loggedInAt: string;
}
