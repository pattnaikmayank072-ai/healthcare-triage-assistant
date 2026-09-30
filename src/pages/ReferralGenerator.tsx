import { useState, useEffect } from 'react';
import { useApp } from '@/context/useApp';
import type { QueueItem, ReferralNoteData } from '@/types/triage';
import { generateReferralNote } from '@/services/apiMock';
import { RiskBadge } from '@/components/RiskBadge';
import { Modal } from '@/components/Modal';
import {
  FileText,
  Loader2,
  Copy,
  Check,
  Download,
  Building2,
  User,
  AlertTriangle,
  Printer,
  Stethoscope,
} from 'lucide-react';

interface ReferralProps {
  queueItems: QueueItem[];
  preselectedPatient: QueueItem | null;
}

export function ReferralGenerator({ queueItems, preselectedPatient }: ReferralProps) {
  const { t } = useApp();

  const [selectedId, setSelectedId] = useState('');
  const [fromFacility, setFromFacility] = useState('PHC Sonbhadra');
  const [toFacility, setToFacility] = useState('');
  const [reason, setReason] = useState('');
  const [clinicalSummary, setClinicalSummary] = useState('');
  const [vitals, setVitals] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generatedNote, setGeneratedNote] = useState('');
  const [copied, setCopied] = useState(false);
  const [printModalOpen, setPrintModalOpen] = useState(false);

  useEffect(() => {
    if (preselectedPatient) {
      setSelectedId(preselectedPatient.id);
      setReason(preselectedPatient.chiefComplaint);
      setFromFacility(preselectedPatient.facility);
    }
  }, [preselectedPatient]);

  const selectedPatient = queueItems.find((i) => i.id === selectedId) || null;

  const handleGenerate = async () => {
    if (!selectedPatient || !toFacility.trim()) return;
    setGenerating(true);
    setGeneratedNote('');
    try {
      const data: ReferralNoteData = {
        patientName: selectedPatient.patientName,
        patientId: selectedPatient.patientId,
        age: selectedPatient.age,
        gender: selectedPatient.gender,
        fromFacility,
        toFacility,
        reason,
        clinicalSummary: clinicalSummary || selectedPatient.chiefComplaint,
        vitals: vitals || 'To be filled by referring medical officer',
        riskLevel: selectedPatient.riskLevel,
        date: new Date().toISOString(),
      };
      const note = await generateReferralNote(data);
      setGeneratedNote(note);
    } finally {
      setGenerating(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(generatedNote);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    const blob = new Blob([generatedNote], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `referral_${selectedPatient?.patientName || 'patient'}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6 sm:px-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-800 dark:text-slate-100">{t.referral.title}</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{t.referral.subtitle}</p>
      </div>

      {/* Advisory */}
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
        <div>
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">{t.review.advisoryTitle}</p>
          <p className="text-xs text-amber-700 dark:text-amber-400">{t.review.advisoryText}</p>
        </div>
      </div>

      {/* Patient Selection */}
      <section className="card p-5">
        <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-800 dark:text-slate-100">
          <User className="h-5 w-5 text-primary-600 dark:text-primary-400" />
          {t.referral.selectPatient}
        </h2>
        {queueItems.length === 0 ? (
          <p className="text-sm text-slate-400">{t.referral.noPatients}</p>
        ) : (
          <select className="input-field" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
            <option value="">-- {t.referral.selectPatient} --</option>
            {queueItems.map((item) => (
              <option key={item.id} value={item.id}>
                {item.patientName} - {item.chiefComplaint} ({item.riskLevel.toUpperCase()})
              </option>
            ))}
          </select>
        )}

        {selectedPatient && (
          <div className="mt-3 flex items-center gap-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50">
            <div className="flex-1">
              <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{selectedPatient.patientName}</p>
              <p className="text-xs text-slate-400">
                {selectedPatient.age} yrs - {selectedPatient.village} - {selectedPatient.chiefComplaint}
              </p>
            </div>
            <RiskBadge level={selectedPatient.riskLevel} size="sm" />
          </div>
        )}
      </section>

      {/* Referral Details */}
      {selectedPatient && (
        <section className="card p-5">
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-slate-800 dark:text-slate-100">
            <Building2 className="h-5 w-5 text-primary-600 dark:text-primary-400" />
            {t.referral.fillDetails}
          </h2>
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.referral.fromFacility}</label>
                <input className="input-field" value={fromFacility} onChange={(e) => setFromFacility(e.target.value)} />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.referral.toFacility}</label>
                <input className="input-field" value={toFacility} onChange={(e) => setToFacility(e.target.value)} placeholder={t.referral.toFacilityPh} />
              </div>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.referral.reason}</label>
              <input className="input-field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t.referral.reasonPh} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.referral.clinicalSummary}</label>
              <textarea className="input-field min-h-[100px] resize-y" value={clinicalSummary} onChange={(e) => setClinicalSummary(e.target.value)} placeholder={t.referral.clinicalSummaryPh} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.referral.vitals}</label>
              <textarea className="input-field min-h-[80px] resize-y" value={vitals} onChange={(e) => setVitals(e.target.value)} placeholder={t.referral.vitalsPh} />
            </div>
            <button onClick={handleGenerate} disabled={generating || !toFacility.trim()} className="btn-primary w-full sm:w-auto">
              {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
              {generating ? t.referral.generating : t.referral.generate}
            </button>
          </div>
        </section>
      )}

      {/* Generated Note */}
      {generatedNote && (
        <section className="card p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-800 dark:text-slate-100">
              <FileText className="h-5 w-5 text-primary-600 dark:text-primary-400" />
              {t.referral.generatedNote}
            </h2>
            <div className="flex gap-2">
              <button onClick={handleCopy} className="btn-ghost text-xs">
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? t.referral.copied : t.referral.copy}
              </button>
              <button onClick={handleDownload} className="btn-ghost text-xs">
                <Download className="h-3.5 w-3.5" />
                {t.referral.download}
              </button>
              <button onClick={() => setPrintModalOpen(true)} className="btn-primary text-xs">
                <Printer className="h-3.5 w-3.5" />
                {t.referral.printExport}
              </button>
            </div>
          </div>
          <pre className="overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs leading-relaxed text-slate-700 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-200 whitespace-pre-wrap font-mono">
            {generatedNote}
          </pre>
        </section>
      )}
      {/* Print / Export Official Referral Sheet Modal */}
      <Modal open={printModalOpen} onClose={() => setPrintModalOpen(false)} title={t.referral.printPreview} maxWidth="max-w-3xl">
        {selectedPatient && generatedNote && (
          <div className="space-y-4">
            <div className="flex justify-end gap-2">
              <button onClick={handlePrint} className="btn-primary text-xs">
                <Printer className="h-3.5 w-3.5" />
                {t.referral.print}
              </button>
              <button onClick={handleDownload} className="btn-ghost text-xs">
                <Download className="h-3.5 w-3.5" />
                {t.referral.download}
              </button>
            </div>
            {/* Official Government Referral Layout */}
            <div className="rounded-lg border-2 border-slate-300 bg-white p-6 dark:border-slate-600 dark:bg-slate-900">
              {/* Header */}
              <div className="flex items-center justify-between border-b-2 border-slate-300 pb-4 dark:border-slate-600">
                <div className="flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-gradient-to-br from-primary-500 to-secondary-600">
                    <Stethoscope className="h-6 w-6 text-white" />
                  </div>
                  <div>
                    <p className="text-sm font-bold uppercase text-slate-800 dark:text-slate-100">{t.referral.govtHeader}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{fromFacility}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{t.referral.govtRef}: REF-{selectedPatient.patientId.slice(0, 8).toUpperCase()}</p>
                  <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{t.referral.govtDate}: {new Date().toLocaleDateString('en-IN')}</p>
                </div>
              </div>

              {/* From / To */}
              <div className="grid grid-cols-2 gap-4 py-4">
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-400">{t.referral.govtFrom}</p>
                  <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{fromFacility}</p>
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-400">{t.referral.govtTo}</p>
                  <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{toFacility}</p>
                </div>
              </div>

              {/* Patient Details */}
              <div className="rounded-md bg-slate-50 p-3 dark:bg-slate-800/50">
                <p className="mb-2 text-xs font-semibold uppercase text-slate-400">{t.referral.govtPatient}</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <div>
                    <p className="text-xs text-slate-400">{t.intake.name}</p>
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{selectedPatient.patientName}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">{t.intake.age}</p>
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{selectedPatient.age} yrs</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">{t.intake.gender}</p>
                    <p className="text-sm font-medium capitalize text-slate-700 dark:text-slate-200">{selectedPatient.gender}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">{t.intake.village}</p>
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{selectedPatient.village || '-'}</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-400">{t.referral.govtRisk}</p>
                    <div className="mt-0.5"><RiskBadge level={selectedPatient.riskLevel} size="sm" /></div>
                  </div>
                </div>
              </div>

              {/* Reason */}
              <div className="py-3">
                <p className="mb-1 text-xs font-semibold uppercase text-slate-400">{t.referral.govtReason}</p>
                <p className="rounded-md bg-slate-50 p-2 text-sm text-slate-700 dark:bg-slate-800/50 dark:text-slate-200">{reason || selectedPatient.chiefComplaint}</p>
              </div>

              {/* Clinical Summary */}
              <div className="py-3">
                <p className="mb-1 text-xs font-semibold uppercase text-slate-400">{t.referral.govtSummary}</p>
                <p className="rounded-md bg-slate-50 p-2 text-sm text-slate-700 dark:bg-slate-800/50 dark:text-slate-200">{clinicalSummary || selectedPatient.chiefComplaint}</p>
              </div>

              {/* Vitals */}
              <div className="py-3">
                <p className="mb-1 text-xs font-semibold uppercase text-slate-400">{t.referral.govtVitals}</p>
                <p className="rounded-md bg-slate-50 p-2 text-sm text-slate-700 dark:bg-slate-800/50 dark:text-slate-200">{vitals || 'To be filled by referring medical officer'}</p>
              </div>

              {/* Advisory */}
              <div className="my-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-900/20">
                <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400" />
                <p className="text-xs text-amber-700 dark:text-amber-400">{t.review.advisoryText}</p>
              </div>

              {/* Signature & Stamp */}
              <div className="flex items-end justify-between border-t-2 border-slate-300 pt-4 dark:border-slate-600">
                <div className="text-center">
                  <div className="flex h-20 w-20 items-center justify-center rounded-full border-2 border-dashed border-slate-300 dark:border-slate-600">
                    <p className="text-[10px] text-slate-400">{t.referral.govtStamp}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-xs text-slate-500 dark:text-slate-400">{t.referral.govtSign}</p>
                  <div className="mt-8 w-48 border-b border-slate-400 dark:border-slate-500" />
                  <p className="mt-1 text-xs font-medium text-slate-600 dark:text-slate-300">Medical Officer, {fromFacility}</p>
                </div>
              </div>
            </div>
          </div>
        )}
        {selectedPatient && !generatedNote && (
          <div className="py-8 text-center">
            <FileText className="mx-auto mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
            <p className="text-sm text-slate-500 dark:text-slate-400">{t.referral.generate}</p>
          </div>
        )}
      </Modal>
    </div>
  );
}
