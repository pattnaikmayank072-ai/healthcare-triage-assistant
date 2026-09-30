import { useState, useCallback } from 'react';
import { AppProvider } from '@/context/AppContext';
import { useApp } from '@/context/useApp';
import { Navbar } from '@/components/Navbar';
import { PatientIntake } from '@/pages/PatientIntake';
import { TriageReview } from '@/pages/TriageReview';
import { QueueDashboard } from '@/pages/QueueDashboard';
import { ReferralGenerator } from '@/pages/ReferralGenerator';
import type { PatientData, QueueItem } from '@/types/triage';
import { ShieldCheck, Database, Wifi } from 'lucide-react';

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

  // Sync queue items for referral generator
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
      {/* Sticky Footer Telemetry & System Status */}
      <footer className="sticky bottom-0 z-30 border-t border-slate-200 bg-white/95 backdrop-blur-md dark:border-slate-700 dark:bg-slate-900/95">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-6 gap-y-1 px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-primary-500" />
            <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.abdm}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-emerald-500" />
            <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.offlineSync}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Wifi className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{t.footer.dpdp}</span>
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
