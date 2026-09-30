import { createContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import type { Role, Language, View, UserSession } from '@/types/triage';
import { getTranslations, type TranslationKeys } from '@/i18n/translations';

interface AppState {
  role: Role;
  setRole: (r: Role) => void;
  language: Language;
  setLanguage: (l: Language) => void;
  theme: 'light' | 'dark';
  toggleTheme: () => void;
  view: View;
  setView: (v: View) => void;
  t: TranslationKeys;
  session: UserSession | null;
  setSession: (s: UserSession | null) => void;
}

export const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<Role>('healthWorker');
  const [language, setLanguage] = useState<Language>('en');
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [view, setView] = useState<View>('intake');
  const [session, setSession] = useState<UserSession | null>(null);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'light' ? 'dark' : 'light'));
  }, []);

  const t = getTranslations(language);

  return (
    <AppContext.Provider
      value={{ role, setRole, language, setLanguage, theme, toggleTheme, view, setView, t, session, setSession }}
    >
      {children}
    </AppContext.Provider>
  );
}
