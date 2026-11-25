import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import commonEn from './locales/en.json';
import commonEl from './locales/el.json';

import { SHIPPED_LOCALES } from './lib/locales';

const syncDocumentLanguage = (language: string | undefined) => {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = (language ?? 'en').split('-')[0];
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: commonEn },
      el: { translation: commonEl },
    },
    supportedLngs: [...SHIPPED_LOCALES],
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    fallbackLng: 'en',
    interpolation: {
      escapeValue: false, // React already protects from XSS
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
    react: {
      useSuspense: false, // Prevents white screen during loading
    },
  });

syncDocumentLanguage(i18n.resolvedLanguage ?? i18n.language);
i18n.on('languageChanged', syncDocumentLanguage);

export default i18n;
