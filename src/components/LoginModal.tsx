import { useState } from 'react';
import { useApp } from '@/context/useApp';
import type { Role, UserSession } from '@/types/triage';
import { Modal } from '@/components/Modal';
import {
  UserRound,
  UserCog,
  Phone,
  ShieldCheck,
  KeyRound,
  Building2,
  Loader2,
  CheckCircle,
  AlertTriangle,
  Fingerprint,
} from 'lucide-react';

const FACILITIES = [
  'PHC Sonbhadra',
  'CHC Medical Unit',
  'DH Robertsganj',
  'PHC Chopan',
  'PHC Obra',
  'PHC Dudhi',
  'PHC Myorpur',
  'PHC Renukoot',
];

interface LoginModalProps {
  open: boolean;
  onClose: () => void;
}

export function LoginModal({ open, onClose }: LoginModalProps) {
  const { t, setRole, setSession, language } = useApp();
  const [mode, setMode] = useState<Role>('healthWorker');
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [facility, setFacility] = useState(FACILITIES[0]);
  const [hprId, setHprId] = useState('');
  const [sigVerified, setSigVerified] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSendOtp = () => {
    if (phone.replace(/\D/g, '').length !== 10) {
      setError(t.auth.invalidPhone);
      return;
    }
    setError('');
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      setOtpSent(true);
    }, 600);
  };

  const handleHprVerify = () => {
    if (hprId.trim().length < 6) {
      setError(t.auth.invalidHpr);
      return;
    }
    setError('');
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      setSigVerified(true);
    }, 800);
  };

  const handleLogin = () => {
    setError('');
    if (mode === 'healthWorker') {
      if (!otpSent || otp.length !== 6) {
        setError(t.auth.invalidOtp);
        return;
      }
    } else {
      if (!hprId.trim() || !sigVerified) {
        setError(t.auth.invalidHpr);
        return;
      }
    }

    setLoading(true);
    setTimeout(() => {
      const session: UserSession = {
        role: mode,
        name: mode === 'doctor' ? 'Dr. ' + (hprId.slice(-4)) : 'HW ' + phone.slice(-4),
        facility: mode === 'doctor' ? 'DH Robertsganj' : facility,
        identifier: mode === 'doctor' ? hprId : phone,
        sigVerified: mode === 'doctor' ? sigVerified : true,
        loggedInAt: new Date().toISOString(),
      };
      setSession(session);
      setRole(mode);
      setLoading(false);
      onClose();
      resetForm();
    }, 500);
  };

  const resetForm = () => {
    setPhone(''); setOtp(''); setOtpSent(false); setHprId(''); setSigVerified(false); setError('');
  };

  return (
    <Modal open={open} onClose={() => { onClose(); resetForm(); }} title={t.auth.title} maxWidth="max-w-md">
      <div className="space-y-5">
        {/* Mode selector */}
        <div>
          <p className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">{t.auth.subtitle}</p>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => { setMode('healthWorker'); setError(''); }}
              className={`flex flex-col items-center gap-1.5 rounded-xl border-2 p-3 transition-all duration-200 ${
                mode === 'healthWorker'
                  ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/30'
                  : 'border-slate-200 hover:border-slate-300 dark:border-slate-600'
              }`}
            >
              <UserRound className={`h-6 w-6 ${mode === 'healthWorker' ? 'text-primary-600 dark:text-primary-400' : 'text-slate-400'}`} />
              <span className={`text-xs font-semibold ${mode === 'healthWorker' ? 'text-primary-700 dark:text-primary-300' : 'text-slate-500'}`}>
                {t.auth.modeHw}
              </span>
            </button>
            <button
              onClick={() => { setMode('doctor'); setError(''); }}
              className={`flex flex-col items-center gap-1.5 rounded-xl border-2 p-3 transition-all duration-200 ${
                mode === 'doctor'
                  ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/30'
                  : 'border-slate-200 hover:border-slate-300 dark:border-slate-600'
              }`}
            >
              <UserCog className={`h-6 w-6 ${mode === 'doctor' ? 'text-primary-600 dark:text-primary-400' : 'text-slate-400'}`} />
              <span className={`text-xs font-semibold ${mode === 'doctor' ? 'text-primary-700 dark:text-primary-300' : 'text-slate-500'}`}>
                {t.auth.modeDoctor}
              </span>
            </button>
          </div>
        </div>

        {/* Health Worker flow */}
        {mode === 'healthWorker' && (
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.auth.phone}</label>
              <div className="flex items-center gap-2">
                <Phone className="h-4 w-4 text-slate-400" />
                <input
                  className="input-field"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder={t.auth.phonePh}
                  maxLength={10}
                  disabled={otpSent}
                />
              </div>
            </div>

            {!otpSent ? (
              <button onClick={handleSendOtp} disabled={loading || phone.length !== 10} className="btn-primary w-full">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                {t.auth.sendOtp}
              </button>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.auth.otp}</label>
                  <input
                    className="input-field tracking-[0.5em] text-center text-lg font-bold"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    placeholder={t.auth.otpPh}
                    maxLength={6}
                  />
                  <p className="mt-1 flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                    <CheckCircle className="h-3 w-3" /> {t.auth.otpSent}
                  </p>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.auth.facilitySelect}</label>
                  <div className="flex items-center gap-2">
                    <Building2 className="h-4 w-4 text-slate-400" />
                    <select className="input-field" value={facility} onChange={(e) => setFacility(e.target.value)}>
                      {FACILITIES.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Doctor flow */}
        {mode === 'doctor' && (
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-slate-300">{t.auth.hprId}</label>
              <div className="flex items-center gap-2">
                <Fingerprint className="h-4 w-4 text-slate-400" />
                <input
                  className="input-field"
                  value={hprId}
                  onChange={(e) => setHprId(e.target.value)}
                  placeholder={t.auth.hprIdPh}
                  disabled={sigVerified}
                />
              </div>
            </div>

            {!sigVerified ? (
              <button onClick={handleHprVerify} disabled={loading || hprId.length < 6} className="btn-primary w-full">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                {t.auth.sigStatus}
              </button>
            ) : (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 dark:border-emerald-800 dark:bg-emerald-900/20">
                <CheckCircle className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                <span className="text-sm font-medium text-emerald-700 dark:text-emerald-300">{t.auth.sigVerified}</span>
              </div>
            )}

            {sigVerified && (
              <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50">
                <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{t.auth.facilitySelect}</p>
                <p className="mt-0.5 text-sm font-medium text-slate-700 dark:text-slate-200">DH Robertsganj (Auto-assigned from HPR)</p>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        )}

        {/* Login button */}
        <button
          onClick={handleLogin}
          disabled={loading || (mode === 'healthWorker' ? !otpSent : !sigVerified)}
          className="btn-primary w-full"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          {t.auth.verify}
        </button>

        <p className="text-center text-xs text-slate-400">
          {language === 'hi' ? 'ABDM अनुपालक सुरक्षित पहुंच' : 'ABDM-compliant secure access'}
        </p>
      </div>
    </Modal>
  );
}
