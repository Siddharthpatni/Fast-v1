import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { LangContext, STORAGE_KEY, initialLang, translate, type Lang, type LangState } from './i18n';

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Not saved; the choice still applies until reload
    }
  }, []);
  // Screen readers pick their voice from <html lang>
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const value = useMemo<LangState>(
    () => ({ lang, setLang, t: (text, vars) => translate(lang, text, vars) }),
    [lang, setLang],
  );
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

