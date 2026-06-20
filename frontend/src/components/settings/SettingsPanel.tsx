import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw, Monitor, Loader2, Info, AlertCircle } from 'lucide-react';
import { toast } from 'react-toastify';
import {
  settingsService,
  SettingDefinition,
  SettingCategory,
  SettingLevel,
} from '../../services/settingsService';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { PageHeader } from '../ui/PageHeader';
import { LoadingState } from '../ui/LoadingState';

interface SettingsPanelProps {
  level: SettingLevel;
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
}

const LEVEL_LABEL_KEY: Record<SettingLevel, string> = {
  system: 'settings.level_system',
  tenant: 'settings.level_tenant',
  user: 'settings.level_user',
};

export const SettingsPanel: React.FC<SettingsPanelProps> = ({ level, title, subtitle, icon }) => {
  const { t } = useTranslation();
  const theme = useSettingsStore(state => state.theme);
  const notificationsEnabled = useSettingsStore(state => state.notificationsEnabled);
  const setTheme = useSettingsStore(state => state.setTheme);
  const setNotificationsEnabled = useSettingsStore(state => state.setNotificationsEnabled);
  const applyEffectivePatch = useSettingsStore(state => state.applyEffectivePatch);

  const [definitions, setDefinitions] = useState<SettingDefinition[]>([]);
  const [categories, setCategories] = useState<SettingCategory[]>([]);
  const [overrides, setOverrides] = useState<Record<string, any>>({});
  const [effective, setEffective] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      settingsService.getDefinitions(),
      settingsService.getOverrides(level),
      settingsService.getEffective(),
    ])
      .then(([defsRes, overridesRes, effRes]) => {
        if (cancelled) return;
        setDefinitions(defsRes.definitions || []);
        setCategories(defsRes.categories || []);
        setOverrides(overridesRes.settings || {});
        setEffective(effRes.settings || {});
      })
      .catch((e: any) => {
        if (cancelled) return;
        setDefinitions([]);
        setCategories([]);
        setOverrides({});
        setEffective({});
        setError(e?.message || t('settings.load_failed', 'Failed to load settings. Make sure the server is running.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [level, t]);

  const sortedCategories = useMemo(
    () => [...categories].sort((a, b) => a.order - b.order),
    [categories]
  );

  const defsByCategory = useMemo(() => {
    const map: Record<string, SettingDefinition[]> = {};
    [...definitions]
      .sort((a, b) => a.order - b.order)
      .forEach((d) => {
        if (!map[d.category]) map[d.category] = [];
        map[d.category].push(d);
      });
    return map;
  }, [definitions]);

  const isRelevant = (d: SettingDefinition): boolean => {
    // Device settings are only meaningful at the user (personal) level.
    if (d.storage === 'device') return level === 'user';
    return d.allowed_levels.includes(level);
  };

  const resolveDisplayValue = (d: SettingDefinition): { value: any; overridden: boolean } => {
    if (d.storage === 'device') {
      const localVal = d.key === 'appearance.theme' ? theme : notificationsEnabled;
      return { value: localVal, overridden: true };
    }
    const overridden = Object.prototype.hasOwnProperty.call(overrides, d.key);
    if (overridden) return { value: overrides[d.key], overridden: true };
    // Inherited: show the effective resolved value (or default) as context.
    const inherited = d.key in effective ? effective[d.key] : d.default;
    return { value: inherited, overridden: false };
  };

  const writeTiered = async (d: SettingDefinition, value: any, reset = false) => {
    const key = d.key;
    setBusyKey(key);
    const prev = overrides[key];
    // Optimistic panel-state update (what is explicitly set at THIS level)
    setOverrides((cur) => {
      const next = { ...cur };
      if (reset) delete next[key];
      else next[key] = value;
      return next;
    });
    try {
      if (reset) {
        await settingsService.resetOverride(level, key);
      } else {
        await settingsService.updateOverride(level, key, value);
      }
      // For user-level edits, sync the live effective cache so dependent UI
      // (biomarker cards, reference-range toggles, i18n) updates immediately.
      if (level === 'user') {
        const synced = reset ? (key in effective ? effective[key] : d.default) : value;
        setEffective((cur) => ({ ...cur, [key]: synced }));
        applyEffectivePatch(key, synced);
      }
    } catch (e: any) {
      setOverrides((cur) => ({ ...cur, [key]: prev }));
      toast.error(e?.message || t('common.error'));
    } finally {
      setBusyKey(null);
    }
  };

  const writeDevice = async (d: SettingDefinition, value: any) => {
    if (d.key === 'appearance.theme') setTheme(value);
    if (d.key === 'notifications.enabled') setNotificationsEnabled(value);
  };

  const handleChange = (d: SettingDefinition, value: any, reset = false) => {
    if (d.storage === 'device') {
      writeDevice(d, value);
    } else {
      writeTiered(d, value, reset);
    }
  };

  if (loading && definitions.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader title={title} subtitle={subtitle} icon={icon} />
        <LoadingState variant="section" />
      </div>
    );
  }

  if (error && definitions.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader title={title} subtitle={subtitle} icon={icon} />
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-red-100 dark:border-red-900/30 p-8 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
              {t('settings.load_failed', 'Failed to load settings')}
            </p>
            <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{error}</p>
            <button
              onClick={() => {
                setError(null);
                setLoading(true);
                settingsService.getDefinitions().then(async (defsRes) => {
                  setDefinitions(defsRes.definitions || []);
                  setCategories(defsRes.categories || []);
                  const [overridesRes, effRes] = await Promise.all([
                    settingsService.getOverrides(level),
                    settingsService.getEffective(),
                  ]);
                  setOverrides(overridesRes.settings || {});
                  setEffective(effRes.settings || {});
                }).catch((e: any) => setError(e?.message || 'Error')).finally(() => setLoading(false));
              }}
              className="mt-3 px-3 py-1.5 text-xs font-bold rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              {t('common.retry', 'Retry')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle={subtitle} icon={icon} />

      {sortedCategories.map((cat) => {
        const defs = (defsByCategory[cat.key] || []).filter(isRelevant);
        if (defs.length === 0) return null;
        return (
          <div
            key={cat.key}
            className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden"
          >
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border">
              <h2 className="text-base font-black text-gray-900 dark:text-dark-text">
                {t(cat.label_key, cat.label_key)}
              </h2>
              {cat.description_key && (
                <p className="text-xs text-gray-500 dark:text-dark-muted mt-0.5">
                  {t(cat.description_key, cat.description_key)}
                </p>
              )}
            </div>

            <div className="divide-y divide-gray-50 dark:divide-dark-border">
              {defs.map((d) => (
                <SettingRow
                  key={d.key}
                  definition={d}
                  level={level}
                  {...resolveDisplayValue(d)}
                  busy={busyKey === d.key}
                  onChange={(value, reset) => handleChange(d, value, reset)}
                  t={t}
                />
              ))}
            </div>
          </div>
        );
      })}

      <p className="text-xs text-gray-400 dark:text-dark-muted px-2 pb-4 flex items-center gap-1.5">
        <Info className="w-3.5 h-3.5" />
        {t('settings.inheritance_hint', 'Higher levels (System → Tenant → User) set defaults. Explicit values here override what is inherited.')}
      </p>
    </div>
  );
};

interface SettingRowProps {
  definition: SettingDefinition;
  level: SettingLevel;
  value: any;
  overridden: boolean;
  busy: boolean;
  onChange: (value: any, reset?: boolean) => void;
  t: any;
}

const SettingRow: React.FC<SettingRowProps> = ({
  definition: d,
  level,
  value,
  overridden,
  busy,
  onChange,
  t,
}) => {
  const isDevice = d.storage === 'device';
  const sourceLabel = isDevice
    ? t('settings.source_device', 'This device')
    : overridden
      ? t(LEVEL_LABEL_KEY[level])
      : t('settings.inherited', 'Inherited');

  return (
    <div className="px-6 py-4 flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-bold text-gray-900 dark:text-dark-text">
            {t(d.label_key, d.label_key)}
          </span>
          <SourceBadge
            label={sourceLabel}
            overridden={overridden}
            isDevice={isDevice}
          />
        </div>
        <p className="text-xs text-gray-500 dark:text-dark-muted mt-0.5">
          {t(d.description_key, d.description_key)}
        </p>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {busy && <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />}
        <SettingControl definition={d} value={value} onChange={onChange} t={t} />
        {!isDevice && overridden && (
          <button
            onClick={() => onChange(null, true)}
            title={t('settings.reset_to_inherited', 'Reset to inherited')}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
};

const SourceBadge: React.FC<{ label: string; overridden: boolean; isDevice: boolean }> = ({ label, overridden, isDevice }) => {
  const cls = isDevice
    ? 'bg-purple-50 text-purple-600 border-purple-100 dark:bg-purple-900/20 dark:text-purple-400 dark:border-purple-900/30'
    : overridden
      ? 'bg-blue-50 text-blue-600 border-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-900/30'
      : 'bg-gray-50 text-gray-500 border-gray-100 dark:bg-dark-bg dark:text-dark-muted dark:border-dark-border';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[9px] font-black uppercase tracking-widest ${cls}`}>
      {isDevice && <Monitor className="w-2.5 h-2.5" />}
      {label}
    </span>
  );
};

interface SettingControlProps {
  definition: SettingDefinition;
  value: any;
  onChange: (value: any, reset?: boolean) => void;
  t: any;
}

const SettingControl: React.FC<SettingControlProps> = ({ definition: d, value, onChange, t }) => {
  const base = 'px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors';

  if (d.type === 'boolean') {
    const on = !!value;
    return (
      <button
        onClick={() => onChange(!on)}
        className={`w-12 h-7 rounded-full relative transition-colors shrink-0 ${on ? 'bg-blue-600' : 'bg-gray-300 dark:bg-dark-border'}`}
        role="switch"
        aria-checked={on}
      >
        <span className={`absolute top-1 w-5 h-5 bg-white rounded-full shadow transition-all ${on ? 'left-6' : 'left-1'}`} />
      </button>
    );
  }

  if (d.type === 'integer' || d.type === 'float') {
    const step = d.type === 'integer' ? 1 : 0.1;
    return (
      <input
        type="number"
        step={step}
        min={d.min ?? undefined}
        max={d.max ?? undefined}
        value={value ?? ''}
        onChange={(e) => {
          const raw = e.target.value;
          onChange(raw === '' ? null : d.type === 'integer' ? parseInt(raw, 10) : parseFloat(raw));
        }}
        className={`${base} w-24 bg-white dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-900 dark:text-dark-text`}
      />
    );
  }

  if (d.type === 'enum') {
    return (
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={`${base} bg-white dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-900 dark:text-dark-text min-w-[140px]`}
      >
        {(d.options || []).map((opt) => (
          <option key={opt.value} value={opt.value}>
            {t(opt.label_key, opt.label_key)}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      type="text"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      className={`${base} w-48 bg-white dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-900 dark:text-dark-text`}
    />
  );
};

export default SettingsPanel;
