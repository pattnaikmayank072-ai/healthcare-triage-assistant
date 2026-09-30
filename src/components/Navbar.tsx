import { useState } from 'react';
import { useApp } from '@/context/useApp';
import type { View, Language } from '@/types/triage';
import { LoginModal } from '@/components/LoginModal';
import {
  Stethoscope,
  ClipboardPlus,
  FileSearch,
  LayoutGrid,
  FileText,
  Moon,
  Sun,
  Languages,
  Wifi,
  LogIn,
  LogOut,
  ShieldCheck,
  UserRound,
  UserCog,
} from 'lucide-react';

export function Navbar() {
  const { role, setRole, language, setLanguage, theme, toggleTheme, view, setView, t, session, setSession } = useApp();
  const [loginOpen, setLoginOpen] = useState(false);

  const navItems: { key: View; label: string; icon: typeof ClipboardPlus }[] = [
    { key: 'intake', label: t.nav.intake, icon: ClipboardPlus },
    { key: 'review', label: t.nav.review, icon: FileSearch },
    { key: 'queue', label: t.nav.queue, icon: LayoutGrid },
    { key: 'referral', label: t.nav.referral, icon: FileText },
  ];

  const doctorOnly: View[] = ['queue', 'review'];

  const handleLogout = () => {
    setSession(null);
    setRole('healthWorker');
    setView('intake');
  };

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur-md dark:border-slate-700 dark:bg-slate-900/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          {/* Logo */}
          <div className="flex items-center gap-2.5">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary-500 to-secondary-600 shadow-md">
              <Stethoscope className="h-5 w-5 text-white" />
            </div>
            <div className="hidden sm:block">
              <h1 className="text-base font-bold leading-tight text-slate-800 dark:text-slate-100">
                {t.appName}
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">{t.appSubtitle}</p>
            </div>
            {/* Online status chip */}
            <div className="hidden items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 dark:bg-emerald-900/30 sm:flex">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <Wifi className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
              <span className="text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                {language === 'en' ? 'Online | Sync Ready' : 'ऑनलाइन | सिंक तैयार'}
              </span>
            </div>
          </div>

          {/* Nav tabs */}
          <nav className="order-3 w-full sm:order-2 sm:w-auto sm:flex-1 sm:justify-center">
            <div className="flex gap-1 overflow-x-auto rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
              {navItems.map((item) => {
                const isDoctorOnly = doctorOnly.includes(item.key);
                const disabled = isDoctorOnly && role !== 'doctor';
                const active = view === item.key;
                return (
                  <button
                    key={item.key}
                    onClick={() => !disabled && setView(item.key)}
                    disabled={disabled}
                    className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-all ${
                      active
                        ? 'bg-white text-primary-700 shadow-sm dark:bg-slate-700 dark:text-primary-300'
                        : disabled
                          ? 'text-slate-300 dark:text-slate-600'
                          : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
                    }`}
                    title={disabled ? t.role.doctor + ' only' : item.label}
                  >
                    <item.icon className="h-4 w-4" />
                    <span className="hidden md:inline">{item.label}</span>
                  </button>
                );
              })}
            </div>
          </nav>

          {/* Controls */}
          <div className="order-2 flex items-center gap-2 sm:order-3">
            {/* Language toggle */}
            <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
              <Languages className="ml-1.5 h-4 w-4 text-slate-400" />
              {(['en', 'hi'] as Language[]).map((l) => (
                <button
                  key={l}
                  onClick={() => setLanguage(l)}
                  className={`rounded-md px-2 py-1 text-xs font-semibold transition-all ${
                    language === l
                      ? 'bg-white text-primary-700 shadow-sm dark:bg-slate-700 dark:text-primary-300'
                      : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
                  }`}
                >
                  {l === 'en' ? 'EN' : 'हिं'}
                </button>
              ))}
            </div>

            {/* Login / Session indicator */}
            {session ? (
              <div className="flex items-center gap-2">
                <div className="hidden items-center gap-1.5 rounded-lg bg-primary-50 px-2.5 py-1.5 dark:bg-primary-900/30 sm:flex">
                  {session.role === 'doctor' ? <UserCog className="h-3.5 w-3.5 text-primary-600 dark:text-primary-400" /> : <UserRound className="h-3.5 w-3.5 text-primary-600 dark:text-primary-400" />}
                  <span className="text-xs font-semibold text-primary-700 dark:text-primary-300">{session.name}</span>
                  {session.sigVerified && <ShieldCheck className="h-3 w-3 text-emerald-500" />}
                </div>
                <button onClick={handleLogout} className="rounded-lg bg-slate-100 p-2 text-slate-500 transition-colors hover:bg-slate-200 hover:text-red-500 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700" aria-label={t.auth.logout}>
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <button onClick={() => setLoginOpen(true)} className="btn-primary text-xs">
                <LogIn className="h-3.5 w-3.5" />
                {t.auth.login}
              </button>
            )}

            {/* Theme toggle */}
            <button
              onClick={toggleTheme}
              className="rounded-lg bg-slate-100 p-2 text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
              aria-label={theme === 'light' ? t.dark : t.light}
            >
              {theme === 'light' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </header>
      <LoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
    </>
  );
}
