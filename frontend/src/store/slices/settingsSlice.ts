import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import i18n from '../../i18n';

interface SettingsState {
  theme: 'light' | 'dark';
  language: string;
  notificationsEnabled: boolean;
  unitSystem: 'metric' | 'imperial';
  
  // AI Settings
  ocrProvider: string;
  ocrApiKey: string;
  ocrApiBase: string;
  ocrModel: string;

  nlpProvider: string;
  nlpApiKey: string;
  nlpApiBase: string;
  nlpModel: string;
  
  // UI Display Preferences
  showReferenceRanges: boolean;
  
  setTheme: (theme: 'light' | 'dark') => void;
  setLanguage: (language: string) => void;
  setNotificationsEnabled: (enabled: boolean) => void;
  setUnitSystem: (system: 'metric' | 'imperial') => void;
  setShowReferenceRanges: (show: boolean) => void;
  
  setOcrSettings: (settings: { provider?: string, apiKey?: string, apiBase?: string, model?: string }) => void;
  setNlpSettings: (settings: { provider?: string, apiKey?: string, apiBase?: string, model?: string }) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'light',
      language: 'en',
      notificationsEnabled: true,
      unitSystem: 'metric',
      
      ocrProvider: 'openai',
      ocrApiKey: '',
      ocrApiBase: 'https://api.openai.com/v1',
      ocrModel: 'gpt-4-vision-preview',

      nlpProvider: 'openai',
      nlpApiKey: '',
      nlpApiBase: 'https://api.openai.com/v1',
      nlpModel: 'gpt-4-turbo-preview',
      
      showReferenceRanges: true, // Default to true
      
      setTheme: (theme) => set({ theme }),
      setLanguage: (language) => {
        set({ language });
        i18n.changeLanguage(language);
      },
      setNotificationsEnabled: (enabled) => set({ notificationsEnabled: enabled }),
      setUnitSystem: (system) => set({ unitSystem: system }),
      setShowReferenceRanges: (show) => set({ showReferenceRanges: show }),
      
      setOcrSettings: (newSettings) => set((state) => ({ ...state, ...newSettings, 
        ocrProvider: newSettings.provider ?? state.ocrProvider,
        ocrApiKey: newSettings.apiKey ?? state.ocrApiKey,
        ocrApiBase: newSettings.apiBase ?? state.ocrApiBase,
        ocrModel: newSettings.model ?? state.ocrModel,
      })),
      
      setNlpSettings: (newSettings) => set((state) => ({ ...state, ...newSettings,
        nlpProvider: newSettings.provider ?? state.nlpProvider,
        nlpApiKey: newSettings.apiKey ?? state.nlpApiKey,
        nlpApiBase: newSettings.apiBase ?? state.nlpApiBase,
        nlpModel: newSettings.model ?? state.nlpModel,
      }))
    }),
    {
      name: 'settings-storage',
    }
  )
);
