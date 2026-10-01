import { useState, useRef, useCallback, useEffect } from 'react';
import { useApp } from '@/context/useApp';
import type { PatientData, OCRResult, UploadedImage, SymptomEvent } from '@/types/triage';
import { submitPatientData, processOCR, transcribeVoiceMock, createSymptomEvent } from '@/services/apiMock';
import {
  Mic,
  Square,
  FileUp,
  ImageUp,
  Plus,
  Trash2,
  Loader2,
  AlertTriangle,
  CheckCircle,
  ArrowRight,
  Activity,
  Eye,
  X,
  QrCode,
  Search,
  ShieldCheck,
  Lock,
  Fingerprint,
} from 'lucide-react';
import { Modal } from '@/components/Modal';

interface IntakeProps {
  onComplete: (data: PatientData) => void;
}

export function PatientIntake({ onComplete }: IntakeProps) {
  const { t, language, setView } = useApp();

  // Patient fields
  const [name, setName] = useState('');
  const [age, setAge] = useState('');
  const [gender, setGender] = useState<'male' | 'female' | 'other'>('male');
  const [phone, setPhone] = useState('');
  const [village, setVillage] = useState('');
  const [facility, setFacility] = useState('PHC Sonbhadra');

  // Clinical fields
  const [chiefComplaint, setChiefComplaint] = useState('');
  const [symptomHistory, setSymptomHistory] = useState('');
  const [voiceTranscript, setVoiceTranscript] = useState('');

  // Voice
  const [isRecording, setIsRecording] = useState(false);
  const [voiceLoading, setVoiceLoading] = useState(false);

  // OCR
  const [ocrResults, setOcrResults] = useState<OCRResult[]>([]);
  const [ocrLoading, setOcrLoading] = useState(false);

  // Images
  const [images, setImages] = useState<UploadedImage[]>([]);
  const [previewImage, setPreviewImage] = useState<UploadedImage | null>(null);

  // OCR Preview Modal
  const [ocrPreview, setOcrPreview] = useState<OCRResult | null>(null);

  // ABHA
  const [abhaNumber, setAbhaNumber] = useState('');
  const [abhaLoading, setAbhaLoading] = useState(false);
  const [abhaVerified, setAbhaVerified] = useState(false);
  const [abhaError, setAbhaError] = useState('');

  // Consent
  const [consentGiven, setConsentGiven] = useState(false);
  const [showConsentTooltip, setShowConsentTooltip] = useState(false);

  // Waveform bars
  const [waveformBars, setWaveformBars] = useState<number[]>(Array.from({ length: 24 }, () => 20));

  // Symptoms timeline
  const [symptoms, setSymptoms] = useState<SymptomEvent[]>([]);
  const [symptomDesc, setSymptomDesc] = useState('');
  const [symptomSeverity, setSymptomSeverity] = useState<SymptomEvent['severity']>('mild');

  // Submit
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const docInputRef = useRef<HTMLInputElement>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);

  const handleVoiceToggle = useCallback(async () => {
    if (isRecording) {
      setIsRecording(false);
      setVoiceLoading(true);
      try {
        const transcript = await transcribeVoiceMock(800);
        setVoiceTranscript((prev) => (prev ? prev + ' ' + transcript : transcript));
      } finally {
        setVoiceLoading(false);
      }
    } else {
      setIsRecording(true);
    }
  }, [isRecording]);

  // Live waveform animation while recording
  useEffect(() => {
    if (!isRecording) {
      setWaveformBars(Array.from({ length: 24 }, () => 20));
      return;
    }
    const interval = setInterval(() => {
      setWaveformBars(Array.from({ length: 24 }, () => Math.floor(Math.random() * 80) + 20));
    }, 120);
    return () => clearInterval(interval);
  }, [isRecording]);

  // ABHA verification
  const handleAbhaVerify = useCallback(async () => {
    const digits = abhaNumber.replace(/\D/g, '');
    if (digits.length !== 14) {
      setAbhaError(t.abha.invalid);
      return;
    }
    setAbhaError('');
    setAbhaLoading(true);
    setAbhaVerified(false);
    setTimeout(() => {
      // Mock: auto-fill patient details from ABHA registry
      setName('Rajesh Kumar Verma');
      setAge('47');
      setGender('male');
      setPhone('9876543210');
      setVillage('Sonbhadra');
      setAbhaVerified(true);
      setAbhaLoading(false);
    }, 1000);
  }, [abhaNumber, t.abha.invalid]);

  const handleAbhaScan = useCallback(() => {
    setAbhaNumber('91-2345-6789-0123');
    setAbhaError('');
  }, []);

  const handleDocUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setOcrLoading(true);
    try {
      const result = await processOCR(file.name);
      setOcrResults((prev) => [...prev, result]);
    } finally {
      setOcrLoading(false);
      if (docInputRef.current) docInputRef.current.value = '';
    }
  }, []);

  const handleImageUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img: UploadedImage = {
        id: Math.random().toString(36).slice(2),
        name: file.name,
        previewUrl: reader.result as string,
        caption: '',
      };
      setImages((prev) => [...prev, img]);
    };
    reader.readAsDataURL(file);
    if (imgInputRef.current) imgInputRef.current.value = '';
  }, []);

  const addSymptom = () => {
    if (!symptomDesc.trim()) return;
    setSymptoms((prev) => [...prev, createSymptomEvent(symptomDesc, symptomSeverity)]);
    setSymptomDesc('');
  };

  const removeSymptom = (id: string) => {
    setSymptoms((prev) => prev.filter((s) => s.id !== id));
  };

  const removeOcr = (idx: number) => {
    setOcrResults((prev) => prev.filter((_, i) => i !== idx));
  };

  const removeImage = (id: string) => {
    setImages((prev) => prev.filter((i) => i.id !== id));
  };

  const handleSubmit = async () => {
    if (!name.trim() || !chiefComplaint.trim()) {
      setError(language === 'hi' ? 'कृपया नाम और मुख्य शिकायत भरें' : 'Please fill in patient name and chief complaint');
      return;
    }
    if (!consentGiven) {
      setError(t.consent.required);
      return;
    }
    setError('');
    setSubmitting(true);
    try {
      const data = await submitPatientData({
        patient: {
          id: Math.random().toString(36).slice(2),
          name,
          age: parseInt(age) || 0,
          gender,
          phone,
          village,
          facility,
        },
        chiefComplaint,
        symptomHistory,
        voiceTranscript,
        symptoms,
        ocrResults,
        images,
      });
      onComplete(data);
      setView('review');
    } catch {
      setError(t.common.error);
    } finally {
      setSubmitting(false);
    }
  };

  const clearForm = () => {
    setName(''); setAge(''); setPhone(''); setVillage('');
    setChiefComplaint(''); setSymptomHistory(''); setVoiceTranscript('');
    setOcrResults([]); setImages([]); setSymptoms([]);
    setError('');
  };

  const flagConfig = {
    high: { icon: ArrowRight, classes: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300', label: 'HIGH' },
    low: { icon: ArrowRight, classes: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300', label: 'LOW' },
    normal: { icon: CheckCircle, classes: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300', label: 'NORMAL' },
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      {/* Header */}
      <div>
        <h1 className="page-title">{t.intake.title}</h1>
        <p className="bilingual-sub">{t.intake.titleHi}</p>
        <p className="page-subtitle">{t.intake.subtitle}</p>
      </div>

      {/* Advisory Banner */}
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">{t.review.advisoryTitle}</p>
          <p className="text-xs text-amber-700 dark:text-amber-400">{t.review.advisoryText}</p>
        </div>
      </div>

      {/* DPDP Consent Toggle */}
      <div className={`card flex items-center justify-between gap-3 p-4 transition-all ${
        !consentGiven ? 'border-amber-300 ring-2 ring-amber-200 dark:border-amber-700 dark:ring-amber-800/50' : ''
      }`}>
        <div className="flex items-center gap-3">
          <div className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
            consentGiven
              ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400'
              : 'bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400'
          }`}>
            <Lock className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{t.consent.title}</p>
            <p className="text-xs text-slate-400">{t.consent.obtained}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowConsentTooltip(!showConsentTooltip)}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
            aria-label="Consent info"
          >
            <AlertTriangle className="h-4 w-4" />
          </button>
          <button
            onClick={() => setConsentGiven(!consentGiven)}
            className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
              consentGiven ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600'
            }`}
            aria-label={t.consent.obtained}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                consentGiven ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        </div>
      </div>
      {showConsentTooltip && (
        <div className="card -mt-2 flex items-start gap-2 border-l-4 border-l-amber-400 p-3">
          <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
          <p className="text-xs text-slate-600 dark:text-slate-300">{t.consent.tooltip}</p>
        </div>
      )}
      {!consentGiven && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-400">
          <Lock className="h-3.5 w-3.5" />
          {t.consent.required}
        </div>
      )}

      {/* ABHA Verification Widget */}
      <section className="card p-5">
        <h2 className="section-title mb-1">
          <Fingerprint className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.abha.title}
        </h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">{t.abha.subtitle}</p>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.abha.lookup}</label>
            <div className="flex items-center gap-2">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                className="input-field"
                value={abhaNumber}
                onChange={(e) => { setAbhaNumber(e.target.value); setAbhaVerified(false); setAbhaError(''); }}
                placeholder={t.abha.abhaPh}
                maxLength={18}
              />
            </div>
          </div>
          <button onClick={handleAbhaScan} className="btn-ghost whitespace-nowrap" title={t.abha.scanQr}>
            <QrCode className="h-4 w-4" />
            {t.abha.scanQr}
          </button>
          <button onClick={handleAbhaVerify} disabled={abhaLoading || abhaVerified} className="btn-primary whitespace-nowrap">
            {abhaLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : abhaVerified ? <CheckCircle className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
            {abhaLoading ? t.abha.verifying : abhaVerified ? t.abha.verified : t.abha.verify}
          </button>
        </div>
        {abhaError && (
          <p className="mt-2 flex items-center gap-1 text-xs text-red-500">
            <AlertTriangle className="h-3 w-3" /> {abhaError}
          </p>
        )}
        {abhaVerified && (
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-800 dark:bg-emerald-900/20">
            <CheckCircle className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
            <span className="text-sm text-emerald-700 dark:text-emerald-300">{t.abha.verifiedSuccess}</span>
          </div>
        )}
      </section>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* Patient Details */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">1</span>
          {t.intake.patientDetails}
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.name}</label>
            <input className="input-field" value={name} onChange={(e) => setName(e.target.value)} placeholder={t.intake.name} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.age}</label>
            <input type="number" className="input-field" value={age} onChange={(e) => setAge(e.target.value)} placeholder={t.intake.age} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.gender}</label>
            <select className="input-field" value={gender} onChange={(e) => setGender(e.target.value as typeof gender)}>
              <option value="male">{t.intake.male}</option>
              <option value="female">{t.intake.female}</option>
              <option value="other">{t.intake.other}</option>
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.phone}</label>
            <input className="input-field" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91" />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.village}</label>
            <input className="input-field" value={village} onChange={(e) => setVillage(e.target.value)} placeholder={t.intake.village} />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.facility}</label>
            <input className="input-field" value={facility} onChange={(e) => setFacility(e.target.value)} placeholder={t.intake.facility} />
          </div>
        </div>
      </section>

      {/* Chief Complaint & History */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">2</span>
          {t.intake.chiefComplaint}
        </h2>
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.chiefComplaint}</label>
            <textarea
              className="input-field min-h-[80px] resize-y"
              value={chiefComplaint}
              onChange={(e) => setChiefComplaint(e.target.value)}
              placeholder={t.intake.chiefComplaintPh}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.intake.symptomHistory}</label>
            <textarea
              className="input-field min-h-[100px] resize-y"
              value={symptomHistory}
              onChange={(e) => setSymptomHistory(e.target.value)}
              placeholder={t.intake.symptomHistoryPh}
            />
          </div>
        </div>
      </section>

      {/* Voice Input */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">3</span>
          {t.intake.voiceInput}
        </h2>
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            {isRecording && (
              <>
                <span className="absolute inset-0 animate-pulse-ring rounded-full bg-red-400" />
                <span className="absolute inset-0 animate-pulse-ring rounded-full bg-red-400" style={{ animationDelay: '0.5s' }} />
              </>
            )}
            <button
              onClick={handleVoiceToggle}
              disabled={voiceLoading || !consentGiven}
              className={`relative flex h-16 w-16 items-center justify-center rounded-full transition-all ${
                isRecording
                  ? 'bg-red-500 text-white shadow-lg shadow-red-500/30'
                  : 'bg-primary-100 text-primary-600 hover:bg-primary-200 dark:bg-primary-800 dark:text-primary-200 dark:hover:bg-primary-700'
              }`}
              aria-label={isRecording ? t.intake.voiceStop : t.intake.voiceStart}
            >
              {voiceLoading ? <Loader2 className="h-6 w-6 animate-spin" /> : isRecording ? <Square className="h-6 w-6" /> : <Mic className="h-6 w-6" />}
            </button>
          </div>

          {/* Live waveform animation */}
          {isRecording && (
            <div className="flex h-16 items-center gap-0.5 rounded-lg bg-slate-900 px-4 py-2 dark:bg-slate-900">
              {waveformBars.map((h, i) => (
                <div
                  key={i}
                  className="wave-bar w-1 rounded-full bg-gradient-to-t from-primary-400 to-red-400"
                  style={{ height: `${h}%`, animationDelay: `${i * 0.05}s` }}
                />
              ))}
            </div>
          )}

          <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
            {isRecording ? t.intake.voiceListening : voiceLoading ? t.common.loading : t.intake.voiceStart}
          </p>
          {voiceLoading && (
            <div className="w-full space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/50">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton h-4 w-full" />
              <div className="skeleton h-4 w-3/4" />
            </div>
          )}
          {voiceTranscript && (
            <div className="w-full rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/50">
              <p className="mb-1 text-xs font-semibold text-slate-500 dark:text-slate-400">{t.intake.voiceTranscript}</p>
              <p className="text-sm text-slate-700 dark:text-slate-200">{voiceTranscript}</p>
            </div>
          )}
        </div>
      </section>

      {/* Document Upload / OCR */}
      <section className="card p-5">
        <h2 className="section-title mb-1">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">4</span>
          {t.intake.documentUpload}
        </h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">{t.intake.documentUploadHint}</p>
        <input ref={docInputRef} type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={handleDocUpload} className="hidden" />
        <button
          onClick={() => docInputRef.current?.click()}
          disabled={ocrLoading || !consentGiven}
          className="btn-secondary w-full border-2 border-dashed border-slate-300 bg-transparent py-6 dark:border-slate-600"
        >
          {ocrLoading ? <Loader2 className="h-5 w-5 animate-spin" /> : <FileUp className="h-5 w-5" />}
          {ocrLoading ? t.intake.processing : t.intake.uploadDoc}
        </button>

        {ocrResults.length > 0 && (
          <div className="mt-4 space-y-4">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">{t.intake.extractedValues}</h3>
            {ocrResults.map((ocr, idx) => (
              <div key={idx} className="rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900/50">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Activity className="h-4 w-4 text-primary-600 dark:text-primary-400" />
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{ocr.documentName}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setOcrPreview(ocr)} className="flex items-center gap-1 rounded-md bg-primary-50 px-2 py-1 text-xs font-medium text-primary-700 transition-colors hover:bg-primary-100 dark:bg-primary-900/30 dark:text-primary-300 dark:hover:bg-primary-900/50">
                      <Eye className="h-3 w-3" />
                      {t.intake.preview}
                    </button>
                    <button onClick={() => removeOcr(idx)} className="text-slate-400 hover:text-red-500" aria-label="Remove">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {ocr.labValues.map((lv, i) => {
                    const fc = flagConfig[lv.flag];
                    return (
                      <div key={i} className="flex items-center justify-between rounded-md bg-white px-3 py-2 dark:bg-slate-800">
                        <div>
                          <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                            {language === 'hi' ? lv.labelHi : lv.label}
                          </p>
                          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                            {lv.value} <span className="text-xs font-normal text-slate-400">{lv.unit}</span>
                          </p>
                        </div>
                        <div className="text-right">
                          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${fc.classes}`}>
                            <fc.icon className="h-3 w-3" />
                            {fc.label}
                          </span>
                          <p className="mt-0.5 text-[10px] text-slate-400">Ref: {lv.refRange}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Image Upload */}
      <section className="card p-5">
        <h2 className="section-title mb-1">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">5</span>
          {t.intake.imageUpload}
        </h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">{t.intake.imageUploadHint}</p>
        <input ref={imgInputRef} type="file" accept="image/*" onChange={handleImageUpload} className="hidden" />
        <button
          onClick={() => imgInputRef.current?.click()}
          disabled={!consentGiven}
          className="btn-secondary w-full border-2 border-dashed border-slate-300 bg-transparent py-6 dark:border-slate-600"
        >
          <ImageUp className="h-5 w-5" />
          {t.intake.uploadImage}
        </button>

        {images.length > 0 && (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {images.map((img) => (
              <div key={img.id} className="group relative overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                <img src={img.previewUrl} alt={img.name} className="h-32 w-full object-cover" />
                <div className="absolute inset-0 flex items-center justify-center gap-2 bg-slate-900/60 opacity-0 transition-opacity group-hover:opacity-100">
                  <button onClick={() => setPreviewImage(img)} className="rounded-md bg-white/90 p-1.5 text-slate-700 hover:bg-white" aria-label={t.intake.preview}>
                    <Eye className="h-4 w-4" />
                  </button>
                  <button onClick={() => removeImage(img.id)} className="rounded-md bg-white/90 p-1.5 text-red-600 hover:bg-white" aria-label="Remove">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Symptom Timeline */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-sm font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">6</span>
          {t.intake.addSymptom}
        </h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            className="input-field flex-1"
            value={symptomDesc}
            onChange={(e) => setSymptomDesc(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addSymptom()}
            placeholder={t.intake.symptomDesc}
          />
          <select className="input-field sm:w-32" value={symptomSeverity} onChange={(e) => setSymptomSeverity(e.target.value as SymptomEvent['severity'])}>
            <option value="mild">{t.intake.mild}</option>
            <option value="moderate">{t.intake.moderate}</option>
            <option value="severe">{t.intake.severe}</option>
          </select>
          <button onClick={addSymptom} className="btn-primary whitespace-nowrap">
            <Plus className="h-4 w-4" />
            {t.intake.addSymptom}
          </button>
        </div>

        {symptoms.length > 0 && (
          <div className="mt-4 space-y-2">
            {symptoms.map((s, i) => (
              <div key={s.id} className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900/50">
                <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-primary-100 text-xs font-bold text-primary-700 dark:bg-primary-800 dark:text-primary-200">
                  {i + 1}
                </div>
                <div className="flex-1">
                  <p className="text-sm text-slate-700 dark:text-slate-200">{s.description}</p>
                  <p className="text-xs text-slate-400">{new Date(s.timestamp).toLocaleTimeString()}</p>
                </div>
                <span className={`badge ${
                  s.severity === 'severe' ? 'badge-red' : s.severity === 'moderate' ? 'badge-yellow' : 'badge-green'
                }`}>
                  {s.severity}
                </span>
                <button onClick={() => removeSymptom(s.id)} className="text-slate-400 hover:text-red-500" aria-label="Remove">
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Actions */}
      <div className="flex flex-col gap-3 sm:flex-row sm:justify-end">
        <button onClick={clearForm} className="btn-ghost" disabled={submitting}>
          <Trash2 className="h-4 w-4" />
          {t.intake.clear}
        </button>
        <button onClick={handleSubmit} className="btn-primary" disabled={submitting}>
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
          {submitting ? t.intake.submitting : t.intake.submit}
        </button>
      </div>

      {/* Image Preview Modal */}
      <Modal open={!!previewImage} onClose={() => setPreviewImage(null)} title={previewImage?.name || ''} maxWidth="max-w-2xl">
        {previewImage && (
          <div>
            <img src={previewImage.previewUrl} alt={previewImage.name} className="w-full rounded-lg" />
          </div>
        )}
      </Modal>

      {/* OCR Side-by-Side Preview Modal */}
      <Modal open={!!ocrPreview} onClose={() => setOcrPreview(null)} title={ocrPreview?.documentName || ''} maxWidth="max-w-4xl">
        {ocrPreview && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Left: Document placeholder */}
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">Document</p>
              <div className="flex min-h-[300px] flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-900/50">
                <FileUp className="h-10 w-10 text-slate-300 dark:text-slate-600" />
                <p className="mt-2 text-sm text-slate-400">{ocrPreview.documentName}</p>
                <p className="mt-1 text-xs text-slate-400">OCR extracted at {new Date(ocrPreview.extractedAt).toLocaleTimeString()}</p>
                <pre className="mt-3 w-full overflow-x-auto rounded-md bg-white p-3 text-[10px] leading-relaxed text-slate-500 dark:bg-slate-800 dark:text-slate-400 whitespace-pre-wrap">{ocrPreview.rawText}</pre>
              </div>
            </div>
            {/* Right: Extracted key values */}
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">{t.intake.extractedValues}</p>
              <div className="space-y-2">
                {ocrPreview.labValues.map((lv, i) => {
                  const fc = flagConfig[lv.flag];
                  return (
                    <div key={i} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800">
                      <div>
                        <p className="text-xs font-medium text-slate-600 dark:text-slate-300">{language === 'hi' ? lv.labelHi : lv.label}</p>
                        <p className="text-base font-semibold text-slate-800 dark:text-slate-100">
                          {lv.value} <span className="text-xs font-normal text-slate-400">{lv.unit}</span>
                        </p>
                        <p className="mt-0.5 text-[10px] text-slate-400">Ref: {lv.refRange}</p>
                      </div>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${fc.classes}`}>
                        <fc.icon className="h-3.5 w-3.5" />
                        {fc.label}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
