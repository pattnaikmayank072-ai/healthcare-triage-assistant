import { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from '@/context/useApp';
import type { QueueItem, RiskLevel, TriageStatus, PatientData, AuditEvent } from '@/types/triage';
import { fetchQueue, updateTriageStatus } from '@/services/apiMock';
import { RiskBadge } from '@/components/RiskBadge';
import { Modal } from '@/components/Modal';
import {
  Search,
  Filter,
  CheckCircle,
  Edit3,
  FileText,
  StickyNote,
  Loader2,
  RefreshCw,
  ArrowUpDown,
  AlertTriangle,
  Check,
  X,
  Users,
  AlertOctagon,
  ClipboardCheck,
  Timer,
  Eye,
  History,
  UserRound,
} from 'lucide-react';

interface QueueProps {
  submittedPatient: PatientData | null;
  onReferPatient: (item: QueueItem) => void;
  onItemsChange: (items: QueueItem[]) => void;
}

type SortKey = 'riskScore' | 'createdAt' | 'patientName' | 'age';

export function QueueDashboard({ submittedPatient, onReferPatient, onItemsChange }: QueueProps) {
  const { t } = useApp();
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [riskFilter, setRiskFilter] = useState<RiskLevel | 'all'>('all');
  const [statusFilter, setStatusFilter] = useState<TriageStatus | 'all'>('all');
  const [sortKey, setSortKey] = useState<SortKey>('riskScore');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Modals
  const [overrideItem, setOverrideItem] = useState<QueueItem | null>(null);
  const [notesItem, setNotesItem] = useState<QueueItem | null>(null);
  const [detailItem, setDetailItem] = useState<QueueItem | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideRisk, setOverrideRisk] = useState<RiskLevel>('green');
  const [clinicalNotes, setClinicalNotes] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [toast, setToast] = useState('');

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchQueue();
      setItems(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    onItemsChange(items);
  }, [items, onItemsChange]);

  // Add submitted patient to queue
  useEffect(() => {
    if (submittedPatient) {
      const newItem: QueueItem = {
        id: submittedPatient.patient.id,
        patientName: submittedPatient.patient.name,
        patientId: submittedPatient.patient.id,
        age: submittedPatient.patient.age,
        gender: submittedPatient.patient.gender,
        village: submittedPatient.patient.village,
        chiefComplaint: submittedPatient.chiefComplaint,
        riskLevel: submittedPatient.riskLevel,
        riskScore: submittedPatient.riskScore,
        status: submittedPatient.status,
        createdAt: submittedPatient.createdAt,
        facility: submittedPatient.patient.facility,
      };
      setItems((prev) => [newItem, ...prev.filter((i) => i.id !== newItem.id)]);
    }
  }, [submittedPatient]);

  // Stats
  const stats = useMemo(() => {
    const total = items.length;
    const critical = items.filter((i) => i.riskLevel === 'red').length;
    const pending = items.filter((i) => i.status === 'pending').length;
    const avgTime = total > 0 ? Math.round(8 + Math.random() * 12) : 0;
    return { total, critical, pending, avgTime };
  }, [items]);

  const filtered = useMemo(() => {
    let result = items.filter((item) => {
      const matchSearch =
        !search ||
        item.patientName.toLowerCase().includes(search.toLowerCase()) ||
        item.chiefComplaint.toLowerCase().includes(search.toLowerCase()) ||
        item.village.toLowerCase().includes(search.toLowerCase());
      const matchRisk = riskFilter === 'all' || item.riskLevel === riskFilter;
      const matchStatus = statusFilter === 'all' || item.status === statusFilter;
      return matchSearch && matchRisk && matchStatus;
    });

    result = [...result].sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'riskScore') cmp = a.riskScore - b.riskScore;
      else if (sortKey === 'age') cmp = a.age - b.age;
      else if (sortKey === 'patientName') cmp = a.patientName.localeCompare(b.patientName);
      else if (sortKey === 'createdAt') cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return result;
  }, [items, search, riskFilter, statusFilter, sortKey, sortDir]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2500);
  };

  const handleApprove = async (item: QueueItem) => {
    setActionLoading(true);
    try {
      await updateTriageStatus(item.id, { status: 'approved' });
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, status: 'approved' } : i)));
      showToast(t.queue.queueUpdated);
    } finally {
      setActionLoading(false);
    }
  };

  const handleOverride = async () => {
    if (!overrideItem || !overrideReason.trim()) return;
    setActionLoading(true);
    try {
      await updateTriageStatus(overrideItem.id, {
        status: 'overridden',
        overrideReason,
        overriddenRiskLevel: overrideRisk,
      });
      setItems((prev) =>
        prev.map((i) =>
          i.id === overrideItem.id ? { ...i, status: 'overridden', riskLevel: overrideRisk } : i
        )
      );
      showToast(t.queue.queueUpdated);
      setOverrideItem(null);
      setOverrideReason('');
    } finally {
      setActionLoading(false);
    }
  };

  const handleSaveNotes = async () => {
    if (!notesItem || !clinicalNotes.trim()) return;
    setActionLoading(true);
    try {
      await updateTriageStatus(notesItem.id, { status: notesItem.status, clinicalNotes });
      showToast(t.queue.queueUpdated);
      setNotesItem(null);
      setClinicalNotes('');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRefer = async (item: QueueItem) => {
    setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, status: 'referred' } : i)));
    onReferPatient(item);
    showToast(t.queue.queueUpdated);
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const statusBadge = (status: TriageStatus) => {
    const config = {
      pending: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
      approved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
      overridden: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
      referred: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    };
    return config[status];
  };

  // Build audit events for detail modal
  const buildAuditEvents = (item: QueueItem): AuditEvent[] => {
    const events: AuditEvent[] = [
      {
        id: 'a1',
        timestamp: item.createdAt,
        actor: 'Health Worker',
        actorRole: 'healthWorker',
        action: t.queue.auditSubmitted,
        detail: item.chiefComplaint,
      },
    ];
    if (item.status === 'approved' || item.status === 'overridden' || item.status === 'referred') {
      events.push({
        id: 'a2',
        timestamp: new Date(new Date(item.createdAt).getTime() + 600000).toISOString(),
        actor: 'Medical Officer',
        actorRole: 'doctor',
        action: t.queue.auditReviewed,
        detail: `Risk: ${item.riskLevel.toUpperCase()}, Score: ${item.riskScore}`,
      });
    }
    if (item.status === 'overridden') {
      events.push({
        id: 'a3',
        timestamp: new Date(new Date(item.createdAt).getTime() + 900000).toISOString(),
        actor: 'Medical Officer',
        actorRole: 'doctor',
        action: t.queue.auditOverridden,
        detail: 'Category changed with clinical justification',
      });
    }
    if (item.status === 'referred') {
      events.push({
        id: 'a4',
        timestamp: new Date(new Date(item.createdAt).getTime() + 1200000).toISOString(),
        actor: 'Medical Officer',
        actorRole: 'doctor',
        action: t.queue.auditReferred,
        detail: 'Escalated to tertiary facility',
      });
    }
    return events;
  };

  const statCards = [
    { icon: Users, label: t.queue.statsTotal, value: stats.total, color: 'text-primary-600 dark:text-primary-400', bg: 'bg-primary-50 dark:bg-primary-900/20' },
    { icon: AlertOctagon, label: t.queue.statsCritical, value: stats.critical, color: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-900/20' },
    { icon: ClipboardCheck, label: t.queue.statsPending, value: stats.pending, color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-900/20' },
    { icon: Timer, label: t.queue.statsAvgTime, value: `${stats.avgTime} ${t.queue.minutes}`, color: 'text-secondary-600 dark:text-secondary-400', bg: 'bg-secondary-50 dark:bg-secondary-900/20' },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-5 px-4 py-6 sm:px-6">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">{t.queue.title}</h1>
          <p className="bilingual-sub">{t.queue.titleHi}</p>
          <p className="page-subtitle">{t.queue.subtitle}</p>
        </div>
        <button onClick={loadQueue} disabled={loading} className="btn-ghost self-start">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          {t.common.retry}
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {statCards.map((stat, i) => (
          <div key={i} className="card flex items-center gap-3 p-4">
            <div className={`flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl ${stat.bg}`}>
              <stat.icon className={`h-5 w-5 ${stat.color}`} />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{stat.label}</p>
              <p className="text-xl font-bold text-slate-800 dark:text-slate-100">{stat.value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="card flex flex-col gap-3 p-4 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            className="input-field pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t.queue.search}
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-slate-400" />
          <select
            className="input-field w-auto"
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value as RiskLevel | 'all')}
          >
            <option value="all">{t.queue.filterAll}</option>
            <option value="red">{t.queue.filterRed}</option>
            <option value="yellow">{t.queue.filterYellow}</option>
            <option value="green">{t.queue.filterGreen}</option>
          </select>
          <select
            className="input-field w-auto"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as TriageStatus | 'all')}
          >
            <option value="all">{t.queue.filterStatus}</option>
            <option value="pending">{t.queue.pending}</option>
            <option value="approved">{t.queue.approved}</option>
            <option value="overridden">{t.queue.overridden}</option>
            <option value="referred">{t.queue.referred}</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {loading ? (
          <div className="space-y-3 p-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <div className="skeleton h-8 w-24" />
                <div className="skeleton h-8 w-12" />
                <div className="skeleton h-8 flex-1" />
                <div className="skeleton h-8 w-20" />
                <div className="skeleton h-8 w-24" />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <AlertTriangle className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
            <p className="text-sm text-slate-500 dark:text-slate-400">{t.queue.noResults}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900/50">
                <tr>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">
                    <button onClick={() => toggleSort('patientName')} className="flex items-center gap-1 hover:text-primary-600">
                      {t.queue.name} <ArrowUpDown className="h-3 w-3" />
                    </button>
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">
                    <button onClick={() => toggleSort('age')} className="flex items-center gap-1 hover:text-primary-600">
                      {t.queue.age} <ArrowUpDown className="h-3 w-3" />
                    </button>
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">{t.queue.village}</th>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">{t.queue.complaint}</th>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">
                    <button onClick={() => toggleSort('riskScore')} className="flex items-center gap-1 hover:text-primary-600">
                      {t.queue.risk} <ArrowUpDown className="h-3 w-3" />
                    </button>
                  </th>
                  <th className="px-4 py-3 font-semibold text-slate-600 dark:text-slate-300">{t.queue.status}</th>
                  <th className="px-4 py-3 text-right font-semibold text-slate-600 dark:text-slate-300">{t.queue.actions}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-700/50">
                {filtered.map((item) => (
                  <tr key={item.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setDetailItem(item)}
                          className="font-medium text-slate-800 hover:text-primary-600 dark:text-slate-100 dark:hover:text-primary-400"
                        >
                          {item.patientName}
                        </button>
                      </div>
                      <p className="text-xs text-slate-400">{item.facility}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{item.age}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{item.village}</td>
                    <td className="max-w-[200px] px-4 py-3 text-slate-600 dark:text-slate-300">
                      <p className="truncate">{item.chiefComplaint}</p>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <RiskBadge level={item.riskLevel} size="sm" />
                        <span className="text-xs font-bold text-slate-400">{item.riskScore}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${statusBadge(item.status)}`}>
                        {t.queue[item.status]}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => setDetailItem(item)}
                          className="rounded-lg p-1.5 text-slate-600 transition-colors hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"
                          title={t.queue.detailTitle}
                        >
                          <Eye className="h-4 w-4" />
                        </button>
                        {item.status === 'pending' && (
                          <button
                            onClick={() => handleApprove(item)}
                            disabled={actionLoading}
                            className="rounded-lg p-1.5 text-emerald-600 transition-colors hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-900/30"
                            title={t.queue.approve}
                          >
                            <CheckCircle className="h-4 w-4" />
                          </button>
                        )}
                        <button
                          onClick={() => {
                            setOverrideItem(item);
                            setOverrideRisk(item.riskLevel);
                            setOverrideReason('');
                          }}
                          className="rounded-lg p-1.5 text-amber-600 transition-colors hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-900/30"
                          title={t.queue.override}
                        >
                          <Edit3 className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => {
                            setNotesItem(item);
                            setClinicalNotes('');
                          }}
                          className="rounded-lg p-1.5 text-slate-600 transition-colors hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"
                          title={t.queue.notes}
                        >
                          <StickyNote className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => handleRefer(item)}
                          className="rounded-lg p-1.5 text-blue-600 transition-colors hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30"
                          title={t.queue.refer}
                        >
                          <FileText className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Patient Detail & Audit Trail Modal */}
      <Modal open={!!detailItem} onClose={() => setDetailItem(null)} title={t.queue.detailTitle} maxWidth="max-w-2xl">
        {detailItem && (
          <div className="space-y-5">
            {/* Patient summary */}
            <div className="flex items-center justify-between rounded-lg bg-slate-50 p-4 dark:bg-slate-900/50">
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-100 dark:bg-primary-800">
                  <UserRound className="h-6 w-6 text-primary-600 dark:text-primary-400" />
                </div>
                <div>
                  <p className="text-base font-semibold text-slate-800 dark:text-slate-100">{detailItem.patientName}</p>
                  <p className="text-xs text-slate-400">
                    {detailItem.age} yrs - {detailItem.village} - {detailItem.facility}
                  </p>
                  <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{detailItem.chiefComplaint}</p>
                </div>
              </div>
              <div className="flex flex-col items-end gap-1">
                <RiskBadge level={detailItem.riskLevel} />
                <span className="text-xs font-bold text-slate-400">Score: {detailItem.riskScore}</span>
                <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${statusBadge(detailItem.status)}`}>
                  {t.queue[detailItem.status]}
                </span>
              </div>
            </div>

            {/* Audit Trail Timeline */}
            <div>
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                <History className="h-4 w-4 text-primary-600 dark:text-primary-400" />
                {t.queue.auditHistory}
              </h3>
              <div className="relative space-y-3 pl-6">
                <div className="absolute left-2 top-2 bottom-2 w-0.5 bg-slate-200 dark:bg-slate-700" />
                {buildAuditEvents(detailItem).map((evt) => (
                  <div key={evt.id} className="relative">
                    <div className={`absolute -left-[18px] top-1.5 h-3 w-3 rounded-full border-2 border-white dark:border-slate-800 ${
                      evt.actorRole === 'doctor' ? 'bg-primary-500' : 'bg-secondary-500'
                    }`} />
                    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-900/50">
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{evt.action}</p>
                        <span className={`badge ${
                          evt.actorRole === 'doctor' ? 'badge-green' : 'badge-yellow'
                        }`}>{evt.actor}</span>
                      </div>
                      {evt.detail && <p className="mt-0.5 text-xs text-slate-400">{evt.detail}</p>}
                      <p className="mt-0.5 text-xs text-slate-400">{new Date(evt.timestamp).toLocaleString()}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* Override Modal */}
      <Modal open={!!overrideItem} onClose={() => setOverrideItem(null)} title={t.queue.overrideTitle}>
        {overrideItem && (
          <div className="space-y-4">
            <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{overrideItem.patientName}</p>
              <p className="text-xs text-slate-400">{overrideItem.chiefComplaint}</p>
              <div className="mt-2">
                <RiskBadge level={overrideItem.riskLevel} />
              </div>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.queue.newRisk}</label>
              <div className="flex gap-2">
                {(['red', 'yellow', 'green'] as RiskLevel[]).map((r) => (
                  <button
                    key={r}
                    onClick={() => setOverrideRisk(r)}
                    className={`flex-1 rounded-lg border-2 px-3 py-2 text-sm font-semibold transition-all ${
                      overrideRisk === r
                        ? r === 'red'
                          ? 'border-red-500 bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                          : r === 'yellow'
                            ? 'border-amber-500 bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
                            : 'border-emerald-500 bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                        : 'border-slate-200 text-slate-500 dark:border-slate-600 dark:text-slate-400'
                    }`}
                  >
                    {r === 'red' ? t.review.urgent : r === 'yellow' ? t.review.priority : t.review.routine}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.queue.overrideReason}</label>
              <textarea
                className="input-field min-h-[100px] resize-y"
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder={t.queue.overrideReasonPh}
              />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setOverrideItem(null)} className="btn-ghost">
                <X className="h-4 w-4" />
                {t.queue.cancel}
              </button>
              <button onClick={handleOverride} disabled={actionLoading || !overrideReason.trim()} className="btn-primary">
                {actionLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                {t.queue.confirm}
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Notes Modal */}
      <Modal open={!!notesItem} onClose={() => setNotesItem(null)} title={t.queue.notesTitle}>
        {notesItem && (
          <div className="space-y-4">
            <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{notesItem.patientName}</p>
              <p className="text-xs text-slate-400">{notesItem.chiefComplaint}</p>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.queue.notesTitle}</label>
              <textarea
                className="input-field min-h-[120px] resize-y"
                value={clinicalNotes}
                onChange={(e) => setClinicalNotes(e.target.value)}
                placeholder={t.queue.notesPh}
              />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setNotesItem(null)} className="btn-ghost">
                <X className="h-4 w-4" />
                {t.queue.cancel}
              </button>
              <button onClick={handleSaveNotes} disabled={actionLoading || !clinicalNotes.trim()} className="btn-primary">
                {actionLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                {t.queue.save}
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 animate-slide-up rounded-full bg-slate-800 px-5 py-2.5 text-sm font-medium text-white shadow-lg dark:bg-slate-700">
          {toast}
        </div>
      )}
    </div>
  );
}
