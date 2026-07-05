import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ShieldAlert,
  Plus,
  Trash2,
  Play,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Save,
  X,
  Users,
  User,
  Activity,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import type { Locale } from 'date-fns';
import {
  notificationService,
  type NotificationRule,
  type TargetSpec,
} from '../../services/notificationService';
import type { Biomarker } from '../../types/biomarker';
import type { Patient } from '../../types/patient';
import type { Doctor } from '../../types/clinical';
import biomarkerService from '../../services/biomarkerService';
import { listPatients } from '../../services/patientService';
import { listDoctors } from '../../services/doctorService';
import { useAuthStore } from '../../store/slices/authSlice';

type Operator = '>' | '<' | '>=' | '<=' | '==' | 'out_of_normal';

const OPERATORS: { value: Operator; label: string }[] = [
  { value: '>', label: 'above (>)' },
  { value: '<', label: 'below (<)' },
  { value: '>=', label: 'above or equal (>=)' },
  { value: '<=', label: 'below or equal (<=)' },
  { value: '==', label: 'exactly (==)' },
  { value: 'out_of_normal', label: 'out of normal range' },
];

const SEVERITIES = ['info', 'warning', 'critical'] as const;

interface RuleFormState {
  biomarker_id: string;
  operator: Operator;
  value: string;
  patientIds: string[];
  doctorIds: string[];
  severity: (typeof SEVERITIES)[number];
  cooldown_minutes: number;
  title_template: string;
  body_template: string;
}

const EMPTY_FORM: RuleFormState = {
  biomarker_id: '',
  operator: '>',
  value: '',
  patientIds: [],
  doctorIds: [],
  severity: 'warning',
  cooldown_minutes: 60,
  title_template: '',
  body_template: '',
};

export function NotificationRules({ patientId }: { patientId?: string }) {
  const { t, i18n } = useTranslation();
  const dateLocale: Locale = i18n.language === 'el' ? el : enUS;
  const user = useAuthStore((s) => s.user);

  const [rules, setRules] = useState<NotificationRule[]>([]);
  const [biomarkers, setBiomarkers] = useState<Biomarker[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<RuleFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const fetched = await notificationService.listRules({
        patient_id: patientId,
      });
      setRules(fetched);
    } catch (err) {
      console.error('Failed to load rules', err);
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  const loadOptions = useCallback(async () => {
    try {
      const [bios, pts, docs] = await Promise.all([
        biomarkerService.getAllBiomarkers(),
        listPatients(user?.tenant_id, 200).then((r) => r.items).catch(() => []),
        listDoctors().catch(() => []),
      ]);
      setBiomarkers(bios);
      setPatients(pts);
      setDoctors(docs);
    } catch (err) {
      console.error('Failed to load rule options', err);
    }
  }, [user?.tenant_id]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  useEffect(() => {
    if (showForm && biomarkers.length === 0) loadOptions();
  }, [showForm, biomarkers.length, loadOptions]);

  const selectedBiomarker = useMemo(
    () => biomarkers.find((b) => b.id === form.biomarker_id),
    [biomarkers, form.biomarker_id]
  );

  const resetForm = () => {
    setForm({ ...EMPTY_FORM, patientIds: patientId ? [patientId] : [] });
    setEditingId(null);
    setError(null);
  };

  const startCreate = () => {
    resetForm();
    setShowForm(true);
  };

  const startEdit = (rule: NotificationRule) => {
    loadOptions();
    setForm({
      biomarker_id: rule.biomarker_id ?? '',
      operator: (rule.operator as Operator) ?? '>',
      value: rule.value?.toString() ?? '',
      patientIds: rule.targets.filter((tg) => tg.kind === 'PATIENT').map((tg) => tg.id!),
      doctorIds: rule.targets.filter((tg) => tg.kind === 'DOCTOR').map((tg) => tg.id!),
      severity: (rule.severity as (typeof SEVERITIES)[number]) ?? 'warning',
      cooldown_minutes: rule.cooldown_minutes ?? 60,
      title_template: rule.title_template ?? '',
      body_template: rule.body_template ?? '',
    });
    setEditingId(rule.id);
    setShowForm(true);
    setError(null);
  };

  const buildTargets = (): TargetSpec[] => {
    const targets: TargetSpec[] = [];
    form.patientIds.forEach((id) => targets.push({ kind: 'PATIENT', id }));
    form.doctorIds.forEach((id) => targets.push({ kind: 'DOCTOR', id }));
    if (targets.length === 0 && patientId) targets.push({ kind: 'PATIENT', id: patientId });
    return targets;
  };

  const handleSave = async () => {
    setError(null);
    if (!form.biomarker_id) {
      setError(t('notifications.rules.pick_biomarker', { defaultValue: 'Pick a biomarker.' }));
      return;
    }
    if (form.operator !== 'out_of_normal' && form.value === '') {
      setError(t('notifications.rules.pick_value', { defaultValue: 'Enter a threshold value.' }));
      return;
    }
    const payload: Partial<NotificationRule> & { rule_type: string } = {
      rule_type: form.operator === 'out_of_normal' ? 'OUT_OF_NORMAL_RANGE' : 'BIOMARKER_THRESHOLD',
      biomarker_id: form.biomarker_id,
      operator: form.operator,
      value: form.operator === 'out_of_normal' ? null : parseFloat(form.value),
      patient_id: form.patientIds.length === 1 ? form.patientIds[0] : patientId,
      severity: form.severity,
      cooldown_minutes: form.cooldown_minutes,
      targets: buildTargets(),
      title_template: form.title_template || null,
      body_template: form.body_template || null,
    };
    setSaving(true);
    try {
      if (editingId) {
        await notificationService.updateRule(editingId, payload);
      } else {
        await notificationService.createRule(payload);
      }
      setShowForm(false);
      resetForm();
      await loadRules();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err.message ?? 'Failed to save rule');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (rule: NotificationRule) => {
    try {
      await notificationService.updateRule(rule.id, { enabled: !rule.enabled });
      setRules((prev) =>
        prev.map((r) => (r.id === rule.id ? { ...r, enabled: !r.enabled } : r))
      );
    } catch {
      // ignore
    }
  };

  const handleTest = async (rule: NotificationRule) => {
    try {
      await notificationService.testRule(rule.id);
    } catch {
      // ignore
    }
  };

  const handleDelete = async (rule: NotificationRule) => {
    if (!confirm(t('notifications.rules.confirm_delete', { defaultValue: 'Delete this rule?' })))
      return;
    try {
      await notificationService.deleteRule(rule.id);
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
    } catch {
      // ignore
    }
  };

  const toggleInList = (list: string[], id: string) =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('notifications.rules.subtitle', {
            defaultValue:
              'Get notified when a biomarker crosses a threshold or leaves its normal range.',
          })}
        </p>
        <button
          onClick={startCreate}
          className="flex items-center px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold rounded-lg transition-colors"
        >
          <Plus className="w-4 h-4 mr-1.5" />
          {t('notifications.rules.new', { defaultValue: 'New rule' })}
        </button>
      </div>

      {showForm && (
        <RuleForm
          form={form}
          setForm={setForm}
          biomarkers={biomarkers}
          patients={patients}
          doctors={doctors}
          selectedBiomarker={selectedBiomarker}
          editing={!!editingId}
          saving={saving}
          error={error}
          onSave={handleSave}
          onCancel={() => {
            setShowForm(false);
            resetForm();
          }}
          toggleInList={toggleInList}
        />
      )}

      {loading && rules.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="w-6 h-6 text-blue-600 animate-spin" />
        </div>
      ) : rules.length === 0 ? (
        <div className="py-12 text-center bg-white dark:bg-dark-surface rounded-2xl border-2 border-dashed border-gray-100 dark:border-dark-border">
          <ShieldAlert className="w-10 h-10 text-gray-200 mx-auto mb-3" />
          <p className="text-gray-400">
            {t('notifications.rules.empty', {
              defaultValue: 'No rules yet. Create one to get biomarker alerts.',
            })}
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {rules.map((rule) => {
            const bio = biomarkers.find((b) => b.id === rule.biomarker_id);
            const bioName = bio?.name ?? rule.biomarker_id ?? '?';
            return (
              <li
                key={rule.id}
                className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl p-4 flex items-center justify-between gap-3 group"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div
                    className={`p-2 rounded-lg ${
                      rule.enabled
                        ? 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
                        : 'bg-gray-50 text-gray-400 dark:bg-dark-bg'
                    }`}
                  >
                    <Activity className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">
                      {rule.title_template || bioName}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-dark-muted truncate">
                      {rule.operator === 'out_of_normal' || rule.rule_type === 'OUT_OF_NORMAL_RANGE'
                        ? `${bioName} out of normal range`
                        : `${bioName} ${rule.operator} ${rule.value ?? ''}`}
                      {' · '}
                      {rule.targets.length} recipient{rule.targets.length === 1 ? '' : 's'}
                      {rule.last_fired_at &&
                        ` · last fired ${formatDistanceToNow(new Date(rule.last_fired_at), {
                          addSuffix: true,
                          locale: dateLocale,
                        })}`}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => handleTest(rule)}
                    title={t('notifications.rules.test', { defaultValue: 'Test' })}
                    className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg"
                  >
                    <Play className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => startEdit(rule)}
                    title={t('common.edit')}
                    className="p-2 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
                  >
                    <RefreshCw className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(rule)}
                    title={t('common.delete')}
                    className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleToggle(rule)}
                    title={rule.enabled ? 'Disable' : 'Enable'}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      rule.enabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-dark-border'
                    }`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition ${
                        rule.enabled ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rule form
// ---------------------------------------------------------------------------

interface RuleFormProps {
  form: RuleFormState;
  setForm: React.Dispatch<React.SetStateAction<RuleFormState>>;
  biomarkers: Biomarker[];
  patients: Patient[];
  doctors: Doctor[];
  selectedBiomarker?: Biomarker;
  editing: boolean;
  saving: boolean;
  error: string | null;
  onSave: () => void;
  onCancel: () => void;
  toggleInList: (list: string[], id: string) => string[];
}

function RuleForm({
  form,
  setForm,
  biomarkers,
  patients,
  doctors,
  selectedBiomarker,
  editing,
  saving,
  error,
  onSave,
  onCancel,
  toggleInList,
}: RuleFormProps) {
  const { t } = useTranslation();
  const [biomarkerQuery, setBiomarkerQuery] = useState('');
  const [showPatients, setShowPatients] = useState(false);
  const [showDoctors, setShowDoctors] = useState(false);

  const filteredBiomarkers = useMemo(() => {
    const q = biomarkerQuery.trim().toLowerCase();
    if (!q) return biomarkers.slice(0, 50);
    return biomarkers
      .filter(
        (b) =>
          b.name.toLowerCase().includes(q) ||
          b.slug.toLowerCase().includes(q) ||
          (b.code ?? '').toLowerCase().includes(q)
      )
      .slice(0, 50);
  }, [biomarkers, biomarkerQuery]);

  const update = <K extends keyof RuleFormState>(key: K, value: RuleFormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const labelCls = 'text-[11px] font-bold uppercase tracking-wider text-gray-400 mb-1.5 block';

  return (
    <div className="bg-white dark:bg-dark-surface border border-blue-200 dark:border-blue-900/40 rounded-2xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text">
          {editing
            ? t('notifications.rules.edit', { defaultValue: 'Edit rule' })
            : t('notifications.rules.create', { defaultValue: 'Create biomarker alert' })}
        </h3>
        <button onClick={onCancel} className="p-1 text-gray-400 hover:text-gray-600 rounded">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Biomarker picker */}
      <div>
        <label className={labelCls}>
          {t('notifications.rules.biomarker', { defaultValue: 'Biomarker' })}
        </label>
        <input
          value={biomarkerQuery}
          onChange={(e) => setBiomarkerQuery(e.target.value)}
          placeholder={t('notifications.rules.search_biomarker', {
            defaultValue: 'Search biomarkers (e.g. glucose, heart rate)...',
          })}
          className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text mb-2"
        />
        {selectedBiomarker && (
          <div className="mb-2 text-xs px-3 py-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-700 dark:text-blue-400 flex items-center justify-between">
            <span>
              <strong>{selectedBiomarker.name}</strong>
              {selectedBiomarker.reference_range_min != null &&
                selectedBiomarker.reference_range_max != null &&
                ` · normal ${selectedBiomarker.reference_range_min}–${selectedBiomarker.reference_range_max}${selectedBiomarker.preferred_unit_symbol ?? ''}`}
            </span>
            <button
              onClick={() => update('biomarker_id', '')}
              className="text-blue-500 hover:text-blue-700"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        <div className="max-h-44 overflow-y-auto border border-gray-100 dark:border-dark-border rounded-lg divide-y divide-gray-50 dark:divide-dark-border">
          {filteredBiomarkers.length === 0 ? (
            <p className="px-3 py-4 text-xs text-gray-400 text-center">
              {biomarkers.length === 0 ? 'Loading…' : 'No matches.'}
            </p>
          ) : (
            filteredBiomarkers.map((b) => (
              <button
                key={b.id}
                onClick={() => {
                  update('biomarker_id', b.id);
                  setBiomarkerQuery('');
                }}
                className={`w-full text-left px-3 py-2 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center justify-between ${
                  form.biomarker_id === b.id ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                }`}
              >
                <span className="text-sm text-gray-900 dark:text-dark-text">
                  {b.name}{' '}
                  <span className="text-[10px] text-gray-400 uppercase">{b.coding_system}</span>
                </span>
                <span className="text-[10px] text-gray-400">
                  {b.reference_range_min != null && b.reference_range_max != null
                    ? `${b.reference_range_min}–${b.reference_range_max}`
                    : 'no range'}
                </span>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Condition */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>
            {t('notifications.rules.condition', { defaultValue: 'Condition' })}
          </label>
          <select
            value={form.operator}
            onChange={(e) => update('operator', e.target.value as Operator)}
            className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
          >
            {OPERATORS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls}>
            {t('notifications.rules.threshold', { defaultValue: 'Threshold' })}
          </label>
          <input
            type="number"
            value={form.value}
            onChange={(e) => update('value', e.target.value)}
            disabled={form.operator === 'out_of_normal'}
            placeholder={
              form.operator === 'out_of_normal'
                ? 'uses biomarker range'
                : selectedBiomarker?.preferred_unit_symbol ?? 'value'
            }
            className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text disabled:opacity-50"
          />
        </div>
      </div>

      {/* Recipients */}
      <RecipientPicker
        label={t('notifications.rules.patients', { defaultValue: 'Patients' })}
        icon={<User className="w-3.5 h-3.5" />}
        open={showPatients}
        setOpen={setShowPatients}
        selected={form.patientIds}
        onToggle={(id) => update('patientIds', toggleInList(form.patientIds, id))}
        items={patients.map((p) => ({ id: p.id, label: patientDisplayName(p) }))}
        emptyHint={t('notifications.rules.no_patients', {
          defaultValue: 'Matches every patient you manage.',
        })}
      />
      <RecipientPicker
        label={t('notifications.rules.doctors', { defaultValue: 'Doctors' })}
        icon={<Users className="w-3.5 h-3.5" />}
        open={showDoctors}
        setOpen={setShowDoctors}
        selected={form.doctorIds}
        onToggle={(id) => update('doctorIds', toggleInList(form.doctorIds, id))}
        items={doctors.map((d) => ({ id: d.id, label: d.name }))}
        emptyHint={t('notifications.rules.no_doctors', {
          defaultValue: 'No doctors selected.',
        })}
      />

      {/* Severity + cooldown */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>
            {t('notifications.rules.severity', { defaultValue: 'Severity' })}
          </label>
          <select
            value={form.severity}
            onChange={(e) => update('severity', e.target.value as (typeof SEVERITIES)[number])}
            className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls}>
            {t('notifications.rules.cooldown', { defaultValue: 'Cooldown (min)' })}
          </label>
          <input
            type="number"
            min={0}
            value={form.cooldown_minutes}
            onChange={(e) => update('cooldown_minutes', parseInt(e.target.value || '0', 10))}
            className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
          />
        </div>
      </div>

      {/* Optional message override */}
      <div>
        <label className={labelCls}>
          {t('notifications.rules.title_override', { defaultValue: 'Custom title (optional)' })}
        </label>
        <input
          value={form.title_template}
          onChange={(e) => update('title_template', e.target.value)}
          placeholder={selectedBiomarker?.name ? `${selectedBiomarker.name} alert` : ''}
          className="w-full text-sm border border-gray-200 dark:border-dark-border rounded-lg px-3 py-2 bg-white dark:bg-dark-surface dark:text-dark-text"
        />
      </div>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-2 border-t border-gray-50 dark:border-dark-border">
        <button
          onClick={onCancel}
          className="px-3 py-2 text-xs font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-border rounded-lg"
        >
          {t('common.cancel')}
        </button>
        <button
          onClick={onSave}
          disabled={saving}
          className="flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-bold rounded-lg"
        >
          <Save className="w-3.5 h-3.5 mr-1.5" />
          {saving ? 'Saving…' : editing ? t('common.save') : t('notifications.rules.create', { defaultValue: 'Create rule' })}
        </button>
      </div>
    </div>
  );
}

function RecipientPicker({
  label,
  icon,
  open,
  setOpen,
  selected,
  onToggle,
  items,
  emptyHint,
}: {
  label: string;
  icon: React.ReactNode;
  open: boolean;
  setOpen: (v: boolean) => void;
  selected: string[];
  onToggle: (id: string) => void;
  items: { id: string; label: string }[];
  emptyHint: string;
}) {
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between text-[11px] font-bold uppercase tracking-wider text-gray-400 mb-1.5"
      >
        <span className="flex items-center gap-1.5">
          {icon}
          {label}
          {selected.length > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 normal-case tracking-normal">
              {selected.length}
            </span>
          )}
        </span>
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
      {open && (
        <div className="max-h-36 overflow-y-auto border border-gray-100 dark:border-dark-border rounded-lg divide-y divide-gray-50 dark:divide-dark-border">
          {items.length === 0 ? (
            <p className="px-3 py-3 text-xs text-gray-400">{emptyHint}</p>
          ) : (
            items.map((it) => (
              <label
                key={it.id}
                className="flex items-center px-3 py-2 hover:bg-gray-50 dark:hover:bg-dark-border/30 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(it.id)}
                  onChange={() => onToggle(it.id)}
                  className="mr-2 rounded"
                />
                <span className="text-sm text-gray-900 dark:text-dark-text">{it.label}</span>
              </label>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function patientDisplayName(p: Patient): string {
  const given = (p as any).name?.given?.join(' ') ?? '';
  const family = (p as any).name?.family ?? '';
  return `${given} ${family}`.trim() || (p as any).id;
}
