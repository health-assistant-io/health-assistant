import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Plus, Trash2 } from 'lucide-react';
import { toast } from 'react-toastify';
import { integrationService, ConfigFlowSchema } from '../../services/integrationService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { Modal } from '../ui/Modal';
import {
  IntegrationConfigForm,
  parseIntegrationConfigSchema,
  type IntegrationConfigFieldView,
  type IntegrationConfigSaveInput,
  type IntegrationConfigValidationView,
  type IntegrationConfigValues,
} from '../ui/integrations';

interface Props {
  domain: string;
  integrationId?: string;
  onClose: () => void;
  onSuccess: () => void;
  initialData?: Record<string, any>;
}

// Fields whose value is an array of strings (rendered as a tag/chip editor).
function isArrayField(prop: any): boolean {
  return prop?.type === 'array' && prop?.items?.type === 'string';
}

// Array-of-strings fields constrained to a known set (``items.enum``) —
// health's toggle-card multi-select dialect, kept app-side (the kit form
// renders the charter's free chip array; a constrained multi-select with
// per-option descriptions is domain content).
function isArrayEnumField(prop: any): boolean {
  return isArrayField(prop) && Array.isArray(prop?.items?.enum) && prop.items.enum.length > 0;
}

// Fields whose value is a key-value map (rendered as dynamic rows).
function isKeyValueField(prop: any): boolean {
  return prop?.type === 'object' && prop?.['x-format'] === 'key-value';
}

// JSON-document textareas.
function isJsonField(prop: any): boolean {
  return prop?.format === 'json';
}

// Password-like fields (rendered through the kit's sealed secret slot).
function isPasswordField(prop: any): boolean {
  return prop?.format === 'password' || prop?.['x-secret'] === true;
}

// Generic conditional visibility: a property is shown if the referenced
// field equals (or is contained in) the expected value. Supports both
// `depends_on` (single condition) and `depends_on_any` (list of conditions).
export function isFieldVisible(
  key: string,
  prop: any,
  formData: Record<string, any>
): boolean {
  const conditions: any[] = [];
  if (prop?.depends_on) conditions.push(prop.depends_on);
  if (Array.isArray(prop?.depends_on_any)) conditions.push(...prop.depends_on_any);
  if (conditions.length === 0) return true;
  // Visible if ANY condition matches.
  return conditions.some((cond) => {
    const current = formData[cond.field];
    if (Array.isArray(cond.value)) return cond.value.includes(current);
    return current === cond.value;
  });
}

// A property renders through the kit's IntegrationConfigForm when it fits
// the charter's JSON-schema-subset (desktop's boundary: parsing stays
// host-side, the kit receives field views). Health's extended dialect —
// key-value maps, constrained array enums, conditional visibility, JSON
// documents — stays app-owned below the kit fields.
function isKitField(prop: any): boolean {
  return (
    !isKeyValueField(prop) &&
    !isArrayEnumField(prop) &&
    !isJsonField(prop) &&
    !prop?.depends_on &&
    !Array.isArray(prop?.depends_on_any)
  );
}

const ConfigFlowModal: React.FC<Props> = ({ domain, integrationId, onClose, onSuccess, initialData }) => {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const [schema, setSchema] = useState<ConfigFlowSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<IntegrationConfigValidationView | null>(null);
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    const fetchSchema = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await integrationService.getConfigFlow(domain);
        setSchema(data);

        // Initialize form data with defaults or initialData.
        // Secret fields from initialData (returned masked as '***' by the
        // backend) are replaced with empty strings so the user must re-enter
        // them to confirm a change. Non-secret fields keep their stored value.
        if (data.data_schema?.properties) {
          const initial: Record<string, any> = {};
          Object.entries(data.data_schema.properties).forEach(([key, value]: [string, any]) => {
            const stored = initialData?.[key];
            if (stored !== undefined && !isPasswordField(value) && stored !== '***') {
              initial[key] = stored;
            } else if (value.default !== undefined) {
              initial[key] = value.default;
            } else if (isArrayField(value)) {
              initial[key] = [];
            } else if (isKeyValueField(value)) {
              initial[key] = {};
            } else if (value.type === 'boolean') {
              initial[key] = false;
            } else {
              initial[key] = '';
            }
          });
          setFormData(initial);
        }
      } catch (err: any) {
        setError(err.response?.data?.detail || "Failed to load configuration flow. Ensure this integration is enabled by the system administrator.");
      } finally {
        setLoading(false);
      }
    };
    fetchSchema();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain]);

  const properties = useMemo(
    () => Object.entries(schema?.data_schema?.properties ?? {}) as [string, any][],
    [schema],
  );

  const requiredKeys = useMemo(
    () => new Set<string>((schema?.data_schema?.required as string[]) ?? []),
    [schema],
  );

  const kitNames = useMemo(() => {
    const names = properties.filter(([, prop]) => isKitField(prop)).map(([key]) => key);
    return new Set(names);
  }, [properties]);

  // Kit field views (charter subset) — enum_descriptions ride the view's
  // description channel for the CURRENTLY SELECTED option, keeping health's
  // per-option explanations inside the kit's rendering.
  const kitFields = useMemo<IntegrationConfigFieldView[]>(() => {
    if (kitNames.size === 0) return [];
    const subset: Record<string, any> = {};
    for (const [key, prop] of properties) {
      if (!kitNames.has(key)) continue;
      const normalized = isPasswordField(prop) ? { ...prop, 'x-secret': true } : prop;
      subset[key] = normalized;
    }
    const views = parseIntegrationConfigSchema({
      type: 'object',
      properties: subset,
      required: properties.filter(([key]) => requiredKeys.has(key) && kitNames.has(key)).map(([key]) => key),
    });
    return views.map((view) => {
      const prop = properties.find(([key]) => key === view.name)?.[1];
      const selected = formData[view.name] || prop?.default || prop?.enum?.[0];
      const described = prop?.enum_descriptions?.[selected];
      return described ? { ...view, description: described } : view;
    });
  }, [properties, kitNames, requiredKeys, formData]);

  const secretSlots = useMemo(() => {
    const slots: Record<string, { set: boolean; hint?: string | null }> = {};
    for (const [key, prop] of properties) {
      if (kitNames.has(key) && isPasswordField(prop)) {
        slots[key] = { set: initialData?.[key] === '***' };
      }
    }
    return slots;
  }, [properties, kitNames, initialData]);

  const handleValuesChange = (values: IntegrationConfigValues) => {
    setFormData((prev) => ({ ...prev, ...values }));
  };

  // Health's extended-dialect editors (key-value maps, constrained array
  // enums, JSON documents) — the kit cannot express these; they render
  // app-owned in schema order after the kit fields.
  const renderExtendedField = (key: string, prop: any) => {
    if (!isFieldVisible(key, prop, formData)) return null;
    const required = requiredKeys.has(key);

    if (isKeyValueField(prop)) {
      return (
        <div key={key}>
          <label className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
            {prop.title || key}
            {required && <span className="text-red-500 ml-1">*</span>}
          </label>
          <div className="space-y-2 rounded-xl border border-gray-200 dark:border-dark-border p-3 bg-gray-50 dark:bg-dark-bg">
            {Object.entries(formData[key] || {}).map(([k, v]: [string, any], idx: number) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  type="text"
                  value={k}
                  placeholder="Key"
                  onChange={(e) => setFormData((prev) => {
                    const obj = { ...(prev[key] || {}) };
                    const v0 = obj[k];
                    delete obj[k];
                    obj[e.target.value] = v0;
                    return { ...prev, [key]: obj };
                  })}
                  className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                />
                <input
                  type="text"
                  value={v as string}
                  placeholder="Value"
                  onChange={(e) => setFormData((prev) => ({
                    ...prev,
                    [key]: { ...(prev[key] || {}), [k]: e.target.value },
                  }))}
                  className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                />
                <button
                  type="button"
                  onClick={() => setFormData((prev) => {
                    const obj = { ...(prev[key] || {}) };
                    delete obj[k];
                    return { ...prev, [key]: obj };
                  })}
                  className="text-gray-400 hover:text-red-500 transition-colors"
                  title="Remove"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setFormData((prev) => ({ ...prev, [key]: { ...(prev[key] || {}), '': '' } }))}
              className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400"
            >
              <Plus className="w-3.5 h-3.5" /> Add row
            </button>
          </div>
          {prop.description && <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{prop.description}</p>}
        </div>
      );
    }

    if (isArrayEnumField(prop)) {
      const options: string[] = prop.items.enum;
      return (
        <div key={key}>
          <span className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
            {prop.title || key}
            {required && <span className="text-red-500 ml-1">*</span>}
          </span>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {options.map((opt: string) => {
              const current: string[] = Array.isArray(formData[key]) ? formData[key] : [];
              const selected = current.includes(opt);
              const desc = prop.items.enum_descriptions?.[opt];
              return (
                <button
                  type="button"
                  key={opt}
                  onClick={() => {
                    const next = selected
                      ? current.filter((v: string) => v !== opt)
                      : [...current, opt];
                    // Preserve enum order so the stored value is deterministic.
                    const ordered = options.filter((v: string) => next.includes(v));
                    setFormData((prev) => ({ ...prev, [key]: ordered }));
                  }}
                  className={`flex items-start gap-2 text-left rounded-xl border p-3 transition-colors ${
                    selected
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 ring-2 ring-blue-500/20'
                      : 'border-gray-200 dark:border-dark-border hover:border-gray-300 dark:hover:border-gray-600 bg-white dark:bg-dark-bg'
                  }`}
                >
                  <div className={`mt-0.5 shrink-0 w-4 h-4 rounded-sm flex items-center justify-center ${selected ? 'bg-blue-600 text-white' : 'border border-gray-300 dark:border-gray-600'}`}>
                    {selected && <Check className="w-3 h-3" />}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-gray-900 dark:text-dark-text">{opt}</div>
                    {desc && <div className="text-xs text-gray-500 dark:text-dark-muted mt-0.5">{desc}</div>}
                  </div>
                </button>
              );
            })}
          </div>
          {(formData[key] || []).length === 0 && (
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-2">
              Select at least one record type.
            </p>
          )}
          {prop.description && <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{prop.description}</p>}
        </div>
      );
    }

    if (isJsonField(prop)) {
      return (
        <div key={key}>
          <label htmlFor={key} className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
            {prop.title || key}
            {required && <span className="text-red-500 ml-1">*</span>}
          </label>
          <textarea
            id={key}
            value={formData[key] !== undefined ? formData[key] : ''}
            onChange={(e) => {
              const value = e.target.value;
              setFormData((prev) => ({ ...prev, [key]: value }));
              try {
                JSON.parse(value);
                setJsonErrors((prev) => ({ ...prev, [key]: '' }));
              } catch (err: any) {
                setJsonErrors((prev) => ({ ...prev, [key]: 'Invalid JSON: ' + err.message }));
              }
            }}
            rows={10}
            className={`w-full rounded-xl border px-4 py-2 bg-gray-50 dark:bg-dark-bg text-gray-900 dark:text-dark-text font-mono text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors ${jsonErrors[key] ? 'border-red-500' : 'border-gray-200 dark:border-dark-border'}`}
          />
          {jsonErrors[key] && <p className="text-xs text-red-500 font-medium">{jsonErrors[key]}</p>}
          {prop.description && <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{prop.description}</p>}
        </div>
      );
    }

    // Conditional plain fields (depends_on/depends_on_any on kit values).
    return (
      <div key={key}>
        <label htmlFor={key} className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
          {prop.title || key}
          {required && <span className="text-red-500 ml-1">*</span>}
        </label>
        <input
          id={key}
          type={prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'}
          value={formData[key] !== undefined && typeof formData[key] !== 'object' ? formData[key] : ''}
          min={prop.minimum}
          max={prop.maximum}
          onChange={(e) => setFormData((prev) => ({
            ...prev,
            [key]: prop.type === 'integer'
              ? parseInt(e.target.value, 10)
              : prop.type === 'number'
                ? parseFloat(e.target.value)
                : e.target.value,
          }))}
          className="w-full rounded-xl border border-gray-200 dark:border-dark-border px-4 py-2 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
        />
        {prop.description && <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{prop.description}</p>}
      </div>
    );
  };

  const handleSubmit = async ({ values, secrets }: IntegrationConfigSaveInput) => {
    if (!currentPatient) return;
    const invalidJson = Object.entries(jsonErrors).find(([, err]) => err !== '');
    if (invalidJson) {
      setValidation({ state: 'error', message: `${invalidJson[0]}: ${invalidJson[1]}` });
      return;
    }
    // Health's config-flow endpoint is the validator (G2): values go out,
    // the 400 detail comes back through the kit's validation display.
    const payload: Record<string, any> = { ...formData, ...values };
    for (const [key, prop] of properties) {
      if (isPasswordField(prop)) payload[key] = secrets[key] ?? '';
    }
    try {
      setSubmitting(true);
      setValidation({ state: 'validating' });
      await integrationService.submitConfigFlow(domain, currentPatient.id, payload, integrationId);
      toast.success("Integration configured successfully!");
      onSuccess();
    } catch (err: any) {
      const detail = err.response?.data?.detail || "Failed to save configuration";
      toast.error(detail);
      setValidation({ state: 'error', message: typeof detail === 'string' ? detail : JSON.stringify(detail) });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title={schema?.title || 'Configure Integration'}
      size="md"
    >
      {loading ? (
        <div className="py-8 text-center text-gray-500 dark:text-dark-muted">
          {t('integrations.config.loading', 'Loading configuration UI...')}
        </div>
      ) : error ? (
        <div className="py-4 text-red-600 bg-red-50 dark:bg-red-900/20 rounded-xl px-4">{error}</div>
      ) : (
        <div className="space-y-4">
          {schema?.description && (
            <p className="text-sm text-gray-500 dark:text-dark-muted">{schema.description}</p>
          )}
          <IntegrationConfigForm
            fields={kitFields}
            values={Object.fromEntries(
              Object.entries(formData).filter(([key]) => kitNames.has(key)),
            ) as IntegrationConfigValues}
            onValuesChange={handleValuesChange}
            onSave={handleSubmit}
            validation={validation}
            secretSlots={secretSlots}
            busy={submitting}
            labels={{
              submitLabel: submitting
                ? t('integrations.config.saving', 'Saving...')
                : t('integrations.config.save', 'Save Configuration'),
              cancelLabel: t('integrations.config.cancel', 'Cancel'),
              secretSetLabel: t('integrations.config.secretSet', 'set — leave empty to keep'),
              secretHintLabel: t('integrations.config.secretNotSet', 'not set'),
            }}
            onCancel={onClose}
          />
          {properties.some(([key]) => !kitNames.has(key)) && (
            <div className="space-y-4 border-t border-gray-100 dark:border-dark-border pt-4">
              {properties
                .filter(([key]) => !kitNames.has(key))
                .map(([key, prop]) => renderExtendedField(key, prop))}
            </div>
          )}
          {Object.values(jsonErrors).some((err) => err !== '') && (
            <p role="alert" className="text-xs text-red-500 font-medium">
              {t('integrations.config.invalidJson', 'Fix invalid JSON before saving.')}
            </p>
          )}
        </div>
      )}
    </Modal>
  );
};

export default ConfigFlowModal;
