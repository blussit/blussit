import { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import { en } from './en';
import { hi } from './hi';

type Language = 'en' | 'hi';
type Dictionary = typeof en;

interface CaptainI18nContextProps {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: keyof Dictionary) => string;
}

const CaptainI18nContext = createContext<CaptainI18nContextProps | undefined>(undefined);

export function CaptainI18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => {
    const saved = localStorage.getItem('captainLanguage');
    return (saved === 'hi') ? 'hi' : 'en';
  });

  const setLanguage = (lang: Language) => {
    localStorage.setItem('captainLanguage', lang);
    setLanguageState(lang);
  };

  const t = (key: keyof Dictionary): string => {
    const dict = language === 'hi' ? hi : en;
    return dict[key] || en[key] || key;
  };

  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'captainLanguage') {
        setLanguageState(e.newValue === 'hi' ? 'hi' : 'en');
      }
    };
    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  return (
    <CaptainI18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </CaptainI18nContext.Provider>
  );
}

export function useCaptainTranslation() {
  const context = useContext(CaptainI18nContext);
  if (!context) {
    throw new Error('useCaptainTranslation must be used within a CaptainI18nProvider');
  }
  return context;
}
