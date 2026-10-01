import { useState, useCallback } from 'react';
import { AppProvider } from '@/context/AppContext';
import { useApp } from '@/context/useApp';
import { Navbar } from '@/components/Navbar';
import { PatientIntake } from '@/pages/PatientIntake';
import { TriageReview } from '@/pages/TriageReview';
import { QueueDashboard } from '@/pages/QueueDashboard';
import { ReferralGenerator } from '@/pages/ReferralGenerator';
import type { PatientData, QueueItem } from '@/types/triage';
import { ShieldCheck, Database, Wifi, AlertTriangle, Building2 } from 'lucide-react';

function AppContent() {
  const { view, t } = useApp();
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
      <main className="flex-1">
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
