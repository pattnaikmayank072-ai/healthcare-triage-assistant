import { Activity, Clock3, ListChecks, TrendingUp } from 'lucide-react';

const symptoms = [
  { label: 'Fever / बुखार', count: 28, width: 'w-[82%]' },
  { label: 'Cough / खांसी', count: 21, width: 'w-[62%]' },
  { label: 'Body pain / बदन दर्द', count: 16, width: 'w-[47%]' },
  { label: 'Breathlessness / सांस फूलना', count: 9, width: 'w-[27%]' },
];

export function Analytics() {
  const metrics = [
    { label: "Today's Triage Count", hindi: 'आज का ट्राइएज', value: '64', note: '+12% vs yesterday', icon: ListChecks, tone: 'text-primary-600 dark:text-primary-400', bg: 'bg-primary-50 dark:bg-primary-900/20' },
    { label: 'Urgent Cases %', hindi: 'अति आवश्यक मामले', value: '18.7%', note: '12 cases need review', icon: TrendingUp, tone: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-900/20' },
    { label: 'Avg Doctor Response', hindi: 'औसत डॉक्टर प्रतिक्रिया', value: '11 min', note: 'Within PHC target', icon: Clock3, tone: 'text-secondary-600 dark:text-secondary-400', bg: 'bg-secondary-50 dark:bg-secondary-900/20' },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="page-title">Analytics &amp; Insights</h1>
          <p className="bilingual-sub">अंतर्दृष्टि</p>
          <p className="page-subtitle">A quick operational view of today&apos;s PHC triage workload.</p>
        </div>
        <div className="inline-flex items-center gap-2 self-start rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300">
          <Activity className="h-3.5 w-3.5" /> Live today
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <section key={metric.label} className="card flex items-start justify-between p-5">
              <div>
                <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{metric.label}</p>
                <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{metric.hindi}</p>
                <p className="mt-4 text-3xl font-extrabold tracking-tight text-slate-800 dark:text-slate-100">{metric.value}</p>
                <p className="mt-1 text-xs font-medium text-slate-500 dark:text-slate-400">{metric.note}</p>
              </div>
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${metric.bg}`}><Icon className={`h-5 w-5 ${metric.tone}`} /></div>
            </section>
          );
        })}
      </div>

      <section className="card p-5 sm:p-6">
        <div className="mb-5 flex items-center justify-between gap-3">
          <div>
            <h2 className="section-title">Top Symptoms Breakdown</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">प्रमुख लक्षणों का विवरण · Based on today&apos;s intake</p>
          </div>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">64 cases</span>
        </div>
        <div className="flex flex-col gap-4">
          {symptoms.map((symptom) => (
            <div key={symptom.label}>
              <div className="mb-1.5 flex items-center justify-between text-sm">
                <span className="font-medium text-slate-700 dark:text-slate-200">{symptom.label}</span>
                <span className="font-bold text-slate-600 dark:text-slate-300">{symptom.count}</span>
              </div>
              <div className="h-2.5 rounded-full bg-slate-100 dark:bg-slate-800"><div className={`h-full rounded-full bg-gradient-to-r from-teal-600 to-emerald-500 ${symptom.width}`} /></div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
