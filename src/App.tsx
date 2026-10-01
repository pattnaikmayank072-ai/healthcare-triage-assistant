import { useState, useCallback } from 'react';
import { AppProvider } from '@/context/AppContext';
import { useApp } from '@/context/useApp';
import { Navbar } from '@/components/Navbar';
import { PatientIntake } from '@/pages/PatientIntake';
import { TriageReview } from '@/pages/TriageReview';
import { QueueDashboard } from '@/pages/QueueDashboard';
import { ReferralGenerator } from '@/pages/ReferralGenerator';
import type { PatientData, QueueItem } from '@/types/triage';
import { ShieldCheck, Database, Wifi, AlertTriangle, Building2, Mic2, BrainCircuit, ClipboardCheck, FileText, ArrowRight, Activity } from 'lucide-react';

function AppContent() {
  const { view, setView, t } = useApp();

  const quickActions = [
    { view: 'intake' as const, label: 'New Patient Intake', hindi: 'नया मरीज पंजीकरण', icon: Mic2, tone: 'bg-teal-700 hover:bg-teal-800 text-white' },
    { view: 'queue' as const, label: 'Queue Dashboard', hindi: 'मरीज कतार', icon: ClipboardCheck, tone: 'bg-white hover:bg-teal-50 text-teal-800 border border-teal-200' },
    { view: 'referral' as const, label: 'Referral Generator', hindi: 'रेफरल जनरेटर', icon: FileText, tone: 'bg-white hover:bg-teal-50 text-teal-800 border border-teal-200' },
  ];

  const featureItems = [
    { label: 'Multimodal Intake', hindi: 'वॉयस / OCR', icon: Mic2 },
    { label: 'AI Triage Engine', hindi: 'एआई ट्राइएज', icon: BrainCircuit },
    { label: 'Doctor Review Queue', hindi: 'डॉक्टर समीक्षा कतार', icon: ClipboardCheck },
    { label: 'ABDM Referral Notes', hindi: 'एबीडीएम रेफरल नोट्स', icon: FileText },
  ];
  const [submittedPatient, setSubmittedPatient] = useState<PatientData | null>(null);
  const [referralPatient, setReferralPatient] = useState<QueueItem | null>(null);
  const [queueItems, setQueueItems] = useState<QueueItem[]>([]);

  const handleIntakeComplete = useCallback((data: PatientData) => {
    setSubmittedPatient(data);
  }, []);

  const handleReferPatient = useCallback((item: QueueItem) => {
    setReferralPatient(item);
  }, []);

  const handleQueueLoad = useCallback((items: QueueItem[]) => {
    setQueueItems(items);
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 transition-colors dark:bg-slate-950 dark:text-slate-100">
      <Navbar />
      <section className="border-b border-slate-200 bg-gradient-to-br from-teal-950 via-teal-900 to-emerald-900 text-white dark:border-slate-800">
        <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
          <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-teal-400/30 bg-white/10 px-3 py-1.5 text-xs font-semibold text-teal-100">
                <Activity className="h-3.5 w-3.5" />
                Primary Health Center · Sonbhadra
              </div>
              <h2 className="text-3xl font-extrabold tracking-tight sm:text-4xl">Connected care, closer to home.</h2>
              <p className="mt-2 text-lg font-medium text-teal-100">सुलभ स्वास्थ्य सेवा, आपके घर के पास</p>
              <p className="mt-3 max-w-xl text-sm leading-6 text-teal-100/80">A clinician-led triage workspace for faster intake, safer review, and interoperable referrals.</p>
              <div className="mt-6 flex flex-wrap gap-2">
                {quickActions.map((action) => {
                  const Icon = action.icon;
                  return (
                    <button key={action.view} onClick={() => setView(action.view)} className={`group inline-flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-left text-xs font-bold shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md ${action.tone}`}>
                      <Icon className="h-4 w-4" />
                      <span>{action.label}<span className="block text-[10px] font-medium opacity-70">{action.hindi}</span></span>
                      <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:w-[490px]">
              {featureItems.map((feature) => {
                const Icon = feature.icon;
                return (
                  <div key={feature.label} className="rounded-xl border border-white/10 bg-white/10 p-3 backdrop-blur-sm">
                    <Icon className="mb-5 h-4 w-4 text-teal-200" />
                    <p className="text-xs font-bold leading-4">{feature.label}</p>
                    <p className="mt-1 text-[10px] text-teal-100/70">{feature.hindi}</p>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>
      <main className="flex-1 pb-6">
        {view === 'intake' && <PatientIntake onComplete={handleIntakeComplete} />}
        {view === 'review' && <TriageReview data={submittedPatient} />}
        {view === 'queue' && (
          <QueueDashboard
            submittedPatient={submittedPatient}
            onReferPatient={handleReferPatient}
            onItemsChange={handleQueueLoad}
          />
        )}
        {view === 'referral' && (
          <ReferralGenerator queueItems={queueItems} preselectedPatient={referralPatient} />
        )}
      </main>

      {/* Production-Grade Sticky Footer */}
      <footer className="sticky bottom-0 z-30 border-t border-slate-200 bg-white/95 backdrop-blur-md dark:border-slate-700 dark:bg-slate-900/95">
        <div className="mx-auto flex max-w-7xl flex-col items-center gap-2 px-4 py-2.5 sm:flex-row sm:justify-between sm:px-6">
          {/* Left: Compliance tags */}
          <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
            <div className="flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-primary-600 dark:text-primary-400" />
              <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.abdm}</span>
            </div>
            <span className="hidden text-slate-300 dark:text-slate-600 sm:inline">|</span>
            <div className="flex items-center gap-1.5">
              <Database className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
              <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.offlineSync}</span>
            </div>
            <span className="hidden text-slate-300 dark:text-slate-600 sm:inline">|</span>
            <div className="flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-secondary-600 dark:text-secondary-400" />
              <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.dpdp}</span>
            </div>
          </div>

          {/* Center: Advisory badge */}
          <div className="flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 dark:border-amber-800 dark:bg-amber-900/20">
            <AlertTriangle className="h-3 w-3 text-amber-600 dark:text-amber-400" />
            <span className="text-[10px] font-semibold text-amber-700 dark:text-amber-400">{t.footer.advisory}</span>
          </div>

          {/* Right: Facility & network status */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-400">{t.footer.online}</span>
            </div>
            <span className="text-slate-300 dark:text-slate-600">|</span>
            <div className="flex items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5 text-slate-500 dark:text-slate-400" />
              <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.facility}</span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}

function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}

export default App;
