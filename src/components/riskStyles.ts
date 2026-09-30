import type { RiskLevel } from '@/types/triage';

export function riskColor(level: RiskLevel): string {
  return {
    red: 'text-red-600 dark:text-red-400',
    yellow: 'text-amber-600 dark:text-amber-400',
    green: 'text-emerald-600 dark:text-emerald-400',
  }[level];
}

export function riskBg(level: RiskLevel): string {
  return {
    red: 'bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-800',
    yellow: 'bg-amber-50 border-amber-200 dark:bg-amber-900/20 dark:border-amber-800',
    green: 'bg-emerald-50 border-emerald-200 dark:bg-emerald-900/20 dark:border-emerald-800',
  }[level];
}
