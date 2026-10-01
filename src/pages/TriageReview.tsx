import { useApp } from '@/context/useApp';
import type { PatientData } from '@/types/triage';
import { RiskBadge } from '@/components/RiskBadge';
import { riskBg } from '@/components/riskStyles';
import {
  AlertTriangle,
  Clock,
  Activity,
  MessageCircleQuestion,
  User,
  Phone,
  MapPin,
  Building2,
  Mic,
  ImageIcon,
  ArrowRight,
  CheckCircle,
  ArrowLeft,
} from 'lucide-react';

interface ReviewProps {
  data: PatientData | null;
}

export function TriageReview({ data }: ReviewProps) {
  const { t, language, setView } = useApp();

  if (!data) {
    return (
      <div className="mx-auto flex max-w-2xl flex-col items-center justify-center px-4 py-20 text-center">
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-slate-100 dark:bg-slate-800">
          <AlertTriangle className="h-8 w-8 text-slate-400" />
        </div>
        <h2 className="mb-2 text-xl font-semibold text-slate-700 dark:text-slate-200">{t.review.noData}</h2>
        <button onClick={() => setView('intake')} className="btn-primary mt-4">
          <ArrowRight className="h-4 w-4" />
          {t.review.goToIntake}
        </button>
      </div>
    );
  }

  const riskLabel = data.riskLevel === 'red' ? t.review.urgent : data.riskLevel === 'yellow' ? t.review.priority : t.review.routine;

  const flagConfig = {
    high: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
    low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    normal: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      {/* Header */}
      <div>
        <h1 className="page-title">{t.review.title}</h1>
        <p className="bilingual-sub">{t.review.titleHi}</p>
        <p className="page-subtitle">{t.review.subtitle}</p>
      </div>

      {/* Advisory Banner */}
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">{t.review.advisoryTitle}</p>
          <p className="text-xs text-amber-700 dark:text-amber-400">{t.review.advisoryText}</p>
        </div>
      </div>

      {/* Risk Score Card */}
      <div className={`card border-2 p-6 ${riskBg(data.riskLevel)}`}>
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:justify-between">
          <div className="flex items-center gap-4">
            <div className="relative flex h-20 w-20 items-center justify-center">
              <svg className="h-20 w-20 -rotate-90" viewBox="0 0 80 80">
                <circle cx="40" cy="40" r="34" fill="none" stroke="currentColor" strokeWidth="6" className="text-slate-200 dark:text-slate-700" />
                <circle
                  cx="40" cy="40" r="34" fill="none"
                  stroke="currentColor"
                  strokeWidth="6"
                  strokeDasharray={`${(data.riskScore / 100) * 213.6} 213.6`}
                  strokeLinecap="round"
                  className={
                    data.riskLevel === 'red' ? 'text-red-500' : data.riskLevel === 'yellow' ? 'text-amber-500' : 'text-emerald-500'
                  }
                />
              </svg>
              <span className="absolute text-xl font-bold text-slate-800 dark:text-slate-100">{data.riskScore}</span>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{t.review.riskScore}</p>
              <p className="text-2xl font-bold text-slate-800 dark:text-slate-100">{riskLabel}</p>
              <RiskBadge level={data.riskLevel} />
            </div>
          </div>
          <button onClick={() => setView('queue')} className="btn-primary">
            {t.nav.queue}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Patient Info */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <User className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.review.patientInfo}
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <InfoItem icon={User} label={t.intake.name} value={data.patient.name} />
          <InfoItem icon={Clock} label={t.intake.age} value={`${data.patient.age} yrs`} />
          <InfoItem icon={User} label={t.intake.gender} value={data.patient.gender === 'male' ? t.intake.male : data.patient.gender === 'female' ? t.intake.female : t.intake.other} />
          <InfoItem icon={Phone} label={t.intake.phone} value={data.patient.phone || '-'} />
          <InfoItem icon={MapPin} label={t.intake.village} value={data.patient.village || '-'} />
          <InfoItem icon={Building2} label={t.intake.facility} value={data.patient.facility} />
        </div>
        <div className="mt-4 space-y-3">
          <div>
            <p className="mb-1 text-xs font-semibold text-slate-500 dark:text-slate-400">{t.intake.chiefComplaint}</p>
            <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-900/50 dark:text-slate-200">{data.chiefComplaint}</p>
          </div>
          {data.symptomHistory && (
            <div>
              <p className="mb-1 text-xs font-semibold text-slate-500 dark:text-slate-400">{t.intake.symptomHistory}</p>
              <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-900/50 dark:text-slate-200">{data.symptomHistory}</p>
            </div>
          )}
        </div>
      </section>

      {/* Voice Transcript */}
      {data.voiceTranscript && (
        <section className="card p-5">
          <h2 className="section-title mb-3">
            <Mic className="h-5 w-5 text-primary-600 dark:text-primary-400" />
            {t.review.voiceTranscript}
          </h2>
          <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-900/50 dark:text-slate-200">{data.voiceTranscript}</p>
        </section>
      )}

      {/* Symptom Timeline */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <Clock className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.review.symptomTimeline}
        </h2>
        {data.symptoms.length > 0 ? (
          <div className="relative space-y-4 pl-6">
            <div className="absolute left-2 top-2 bottom-2 w-0.5 bg-slate-200 dark:bg-slate-700" />
            {data.symptoms.map((s) => (
              <div key={s.id} className="relative">
                <div className={`absolute -left-[18px] top-1.5 h-3 w-3 rounded-full border-2 border-white dark:border-slate-800 ${
                  s.severity === 'severe' ? 'bg-red-500' : s.severity === 'moderate' ? 'bg-amber-500' : 'bg-emerald-500'
                }`} />
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900/50">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{s.description}</p>
                    <span className={`badge ${
                      s.severity === 'severe' ? 'badge-red' : s.severity === 'moderate' ? 'badge-yellow' : 'badge-green'
                    }`}>{s.severity}</span>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-400">{new Date(s.timestamp).toLocaleString()}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">{t.review.noReports}</p>
        )}
      </section>

      {/* Extracted Key Values */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <Activity className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.review.keyValues}
        </h2>
        {data.ocrResults.length > 0 ? (
          <div className="space-y-4">
            {data.ocrResults.map((ocr, idx) => (
              <div key={idx}>
                <p className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">{ocr.documentName}</p>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {ocr.labValues.map((lv, i) => (
                    <div key={i} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900/50">
                      <div>
                        <p className="text-xs font-medium text-slate-600 dark:text-slate-300">{language === 'hi' ? lv.labelHi : lv.label}</p>
                        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                          {lv.value}<span className="ml-1 text-xs font-normal text-slate-400">{lv.unit}</span>
                        </p>
                      </div>
                      <div className="text-right">
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${flagConfig[lv.flag]}`}>
                          {lv.flag === 'normal' ? <CheckCircle className="h-3 w-3" /> : <ArrowRight className="h-3 w-3" />}
                          {lv.flag.toUpperCase()}
                        </span>
                        <p className="mt-0.5 text-[10px] text-slate-400">Ref: {lv.refRange}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">{t.review.noReports}</p>
        )}
      </section>

      {/* Follow-up Questions */}
      <section className="card p-5">
        <h2 className="section-title mb-4">
          <MessageCircleQuestion className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.review.followUpQuestions}
        </h2>
        {data.followUpQuestions.length > 0 ? (
          <div className="space-y-2">
            {data.followUpQuestions.map((q, i) => (
              <div key={q.id} className="flex items-start gap-3 rounded-lg border border-primary-100 bg-primary-50 px-4 py-3 dark:border-primary-800 dark:bg-primary-900/20">
                <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary-600 text-xs font-bold text-white">{i + 1}</span>
                <p className="text-sm text-slate-700 dark:text-slate-200">{language === 'hi' ? q.questionHi : q.question}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">{t.review.noFollowUps}</p>
        )}
      </section>

      {/* Images */}
      {data.images.length > 0 && (
        <section className="card p-5">
          <h2 className="section-title mb-4">
            <ImageIcon className="h-5 w-5 text-primary-600 dark:text-primary-400" />
            {t.review.images}
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {data.images.map((img) => (
              <div key={img.id} className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                <img src={img.previewUrl} alt={img.name} className="h-32 w-full object-cover" />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Back */}
      <div className="flex justify-start">
        <button onClick={() => setView('intake')} className="btn-ghost">
          <ArrowLeft className="h-4 w-4" />
          {t.nav.intake}
        </button>
      </div>
    </div>
  );
}

function InfoItem({ icon: Icon, label, value }: { icon: typeof User; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-900/50">
      <Icon className="h-4 w-4 flex-shrink-0 text-slate-400" />
      <div>
        <p className="text-[10px] font-medium uppercase text-slate-400">{label}</p>
        <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{value}</p>
      </div>
    </div>
  );
}
