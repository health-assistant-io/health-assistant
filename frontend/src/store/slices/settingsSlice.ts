import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import i18n from '../../i18n';
import {
  settingsService,
  SettingDefinition,
  SettingCategory,
} from '../../services/settingsService';

const DEFAULT_PRECISION = 0;
const DEFAULT_PRECISION_BELOW_30 = 1;
const DEFAULT_PRECISION_BELOW_10 = 1;
const DEFAULT_PRECISION_BELOW_3 = 2;
const DEFAULT_PRECISION_BELOW_1 = 3;

const EFFECTIVE_TO_FIELD: Record<string, string> = {
  'appearance.biomarker_precision': 'biomarkerPrecision',
  'appearance.precision_below_30': 'precisionBelow30',
  'appearance.precision_below_10': 'precisionBelow10',
  'appearance.precision_below_3': 'precisionBelow3',
  'appearance.precision_below_1': 'precisionBelow1',
  'appearance.show_reference_ranges': 'showReferenceRanges',
  'appearance.show_relative_scores': 'showRelativeScores',
  'appearance.compact_dashboard': 'compactDashboard',
  'appearance.date_format': 'dateFormat',
  'localization.language': 'language',
  'localization.unit_system': 'unitSystem',
};

const FIELD_DEFAULTS: Record<string, any> = {
  biomarkerPrecision: DEFAULT_PRECISION,
  precisionBelow30: DEFAULT_PRECISION_BELOW_30,
  precisionBelow10: DEFAULT_PRECISION_BELOW_10,
  precisionBelow3: DEFAULT_PRECISION_BELOW_3,
  precisionBelow1: DEFAULT_PRECISION_BELOW_1,
  showReferenceRanges: true,
  showRelativeScores: true,
  compactDashboard: false,
  dateFormat: 'YYYY-MM-DD',
  language: 'en',
  unitSystem: 'metric',
};

interface SettingsState {
  // ---------------- Device-local (per-browser) ----------------
  theme: 'light' | 'dark';
  notificationsEnabled: boolean;

  // Legacy OCR/NLP provider fields (kept for compatibility; AI config is managed separately)
  ocrProvider: string;
  ocrApiKey: string;
  ocrApiBase: string;
  ocrModel: string;
  nlpProvider: string;
  nlpApiKey: string;
  nlpApiBase: string;
  nlpModel: string;

  // ---------------- Tiered (USER > TENANT > SYSTEM > default) ----------------
  language: string;
  unitSystem: 'metric' | 'imperial';
  showReferenceRanges: boolean;
  biomarkerPrecision: number;
  precisionBelow30: number;
  precisionBelow10: number;
  precisionBelow3: number;
  precisionBelow1: number;
  showRelativeScores: boolean;
  compactDashboard: boolean;
  dateFormat: string;

  // Raw resolved payload + catalog
  effectiveSettings: Record<string, any>;
  settingsSources: Record<string, string>;
  definitions: SettingDefinition[];
  categories: SettingCategory[];
  settingsLoaded: boolean;
  settingsLoading: boolean;

  // ---------------- Actions ----------------
  loadSettings: () => Promise<void>;
  getSetting: (key: string) => any;
  getSettingSource: (key: string) => string;
  updateUserSetting: (key: string, value: any) => Promise<void>;
  applyEffectivePatch: (key: string, value: any) => void;

  // Device-local setters
  setTheme: (theme: 'light' | 'dark') => void;
  setLanguage: (language: string) => void;
  setNotificationsEnabled: (enabled: boolean) => void;
  setUnitSystem: (system: 'metric' | 'imperial') => void;
  setShowReferenceRanges: (show: boolean) => void;
  setOcrSettings: (settings: { provider?: string; apiKey?: string; apiBase?: string; model?: string }) => void;
  setNlpSettings: (settings: { provider?: string; apiKey?: string; apiBase?: string; model?: string }) => void;
}

function applyEffectiveToFields(state: Partial<SettingsState>, effective: Record<string, any>) {
  const patch: Record<string, any> = { effectiveSettings: effective };
  for (const [key, field] of Object.entries(EFFECTIVE_TO_FIELD)) {
    if (key in effective) patch[field] = effective[key];
  }
  return { ...state, ...patch };
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      theme: 'light',
      notificationsEnabled: true,

      ocrProvider: 'openai',
      ocrApiKey: '',
      ocrApiBase: 'https://api.openai.com/v1',
      ocrModel: 'gpt-4-vision-preview',
      nlpProvider: 'openai',
      nlpApiKey: '',
      nlpApiBase: 'https://api.openai.com/v1',
      nlpModel: 'gpt-4-turbo-preview',

      language: FIELD_DEFAULTS.language,
      unitSystem: FIELD_DEFAULTS.unitSystem,
      showReferenceRanges: FIELD_DEFAULTS.showReferenceRanges,
      biomarkerPrecision: DEFAULT_PRECISION,
      precisionBelow30: DEFAULT_PRECISION_BELOW_30,
      precisionBelow10: DEFAULT_PRECISION_BELOW_10,
      precisionBelow3: DEFAULT_PRECISION_BELOW_3,
      precisionBelow1: DEFAULT_PRECISION_BELOW_1,
      showRelativeScores: FIELD_DEFAULTS.showRelativeScores,
      compactDashboard: FIELD_DEFAULTS.compactDashboard,
      dateFormat: FIELD_DEFAULTS.dateFormat,

      effectiveSettings: {},
      settingsSources: {},
      definitions: [],
      categories: [],
      settingsLoaded: false,
      settingsLoading: false,

      loadSettings: async () => {
        if (get().settingsLoading) return;
        set({ settingsLoading: true });
        try {
          const [defsRes, effRes] = await Promise.all([
            settingsService.getDefinitions().catch(() => null),
            settingsService.getEffective().catch(() => null),
          ]);
          const patch: Partial<SettingsState> = {
            settingsLoaded: true,
            settingsLoading: false,
          };
          if (defsRes) {
            patch.definitions = defsRes.definitions;
            patch.categories = defsRes.categories;
          }
          if (effRes) {
            patch.settingsSources = effRes.sources;
            Object.assign(patch, applyEffectiveToFields(get(), effRes.settings));
            const lang = effRes.settings['localization.language'];
            if (lang && lang !== i18n.language) {
              i18n.changeLanguage(lang).catch(() => undefined);
            }
          }
          set(patch);
        } catch {
          set({ settingsLoaded: true, settingsLoading: false });
        }
      },

      getSetting: (key) => {
        const state = get();
        if (key in state.effectiveSettings) return state.effectiveSettings[key];
        const field = EFFECTIVE_TO_FIELD[key];
        if (field) return (state as any)[field];
        return undefined;
      },

      getSettingSource: (key) => get().settingsSources[key] || 'default',

      updateUserSetting: async (key, value) => {
        const prevEffective = { ...get().effectiveSettings };
        // Optimistic local update
        set((state) => applyEffectiveToFields(state, { ...state.effectiveSettings, [key]: value }) as SettingsState);
        // Keep i18n in sync for language changes
        if (key === 'localization.language') {
          i18n.changeLanguage(value).catch(() => undefined);
        }
        try {
          await settingsService.updateOverride('user', key, value);
        } catch {
          // Revert on hard failure (offline writes resolve via the queue interceptor)
          set((state) => applyEffectiveToFields(state, prevEffective) as SettingsState);
          throw new Error('Failed to save setting');
        }
      },

      applyEffectivePatch: (key, value) => {
        set((state) => applyEffectiveToFields(state, { ...state.effectiveSettings, [key]: value }) as SettingsState);
        if (key === 'localization.language') {
          i18n.changeLanguage(value).catch(() => undefined);
        }
      },

      setTheme: (theme) => set({ theme }),
      setLanguage: (language) => {
        set({ language });
        i18n.changeLanguage(language);
        get().updateUserSetting('localization.language', language).catch(() => undefined);
      },
      setNotificationsEnabled: (enabled) => set({ notificationsEnabled: enabled }),
      setUnitSystem: (system) => {
        set({ unitSystem: system });
        get().updateUserSetting('localization.unit_system', system).catch(() => undefined);
      },
      setShowReferenceRanges: (show) => {
        set({ showReferenceRanges: show });
        get().updateUserSetting('appearance.show_reference_ranges', show).catch(() => undefined);
      },
      setOcrSettings: (newSettings) => set((state) => ({
        ...state,
        ...newSettings,
        ocrProvider: newSettings.provider ?? state.ocrProvider,
        ocrApiKey: newSettings.apiKey ?? state.ocrApiKey,
        ocrApiBase: newSettings.apiBase ?? state.ocrApiBase,
        ocrModel: newSettings.model ?? state.ocrModel,
      })),
      setNlpSettings: (newSettings) => set((state) => ({
        ...state,
        ...newSettings,
        nlpProvider: newSettings.provider ?? state.nlpProvider,
        nlpApiKey: newSettings.apiKey ?? state.nlpApiKey,
        nlpApiBase: newSettings.apiBase ?? state.nlpApiBase,
        nlpModel: newSettings.model ?? state.nlpModel,
      })),
    }),
    {
      name: 'settings-storage',
      // Persist device-local + cached tiered values (for offline instant paint).
      // Do NOT persist flags that must refresh each load.
      partialize: (state) => ({
        theme: state.theme,
        notificationsEnabled: state.notificationsEnabled,
        ocrProvider: state.ocrProvider,
        ocrApiKey: state.ocrApiKey,
        ocrApiBase: state.ocrApiBase,
        ocrModel: state.ocrModel,
        nlpProvider: state.nlpProvider,
        nlpApiKey: state.nlpApiKey,
        nlpApiBase: state.nlpApiBase,
        nlpModel: state.nlpModel,
        language: state.language,
        unitSystem: state.unitSystem,
        showReferenceRanges: state.showReferenceRanges,
        biomarkerPrecision: state.biomarkerPrecision,
        precisionBelow30: state.precisionBelow30,
        precisionBelow10: state.precisionBelow10,
        precisionBelow3: state.precisionBelow3,
        precisionBelow1: state.precisionBelow1,
        showRelativeScores: state.showRelativeScores,
        compactDashboard: state.compactDashboard,
        dateFormat: state.dateFormat,
        effectiveSettings: state.effectiveSettings,
        settingsSources: state.settingsSources,
      }),
    }
  )
);
