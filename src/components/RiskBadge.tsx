import type { RiskLevel } from '@/types/triage';
import { AlertTriangle, Clock, CheckCircle } from 'lucide-react';

export function RiskBadge({
  level,
  size = 'md',
}: {
  level: RiskLevel;
  size?: 'sm' | 'md';
}) {
  const config = {
    red: { icon: AlertTriangle, classes: 'badge-red', dot: 'bg-red-500' },
    yellow: { icon: Clock, classes: 'badge-yellow', dot: 'bg-amber-500' },
    green: { icon: CheckCircle, classes: 'badge-green', dot: 'bg-emerald-500' },
  };
  const { icon: Icon, classes, dot } = config[level];
  const sizeClass = size === 'sm' ? 'text-[10px] px-2 py-0.5' : '';
  return (
    <span className={`${classes} ${sizeClass}`}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
      <Icon className="h-3 w-3" />
      {level.toUpperCase()}
    </span>
  );
}
