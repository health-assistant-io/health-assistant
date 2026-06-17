import React, { useState, useEffect } from 'react';
import { X, Plus, Trash2, Eye, EyeOff } from 'lucide-react';
import { toast } from 'react-toastify';
import { integrationService, ConfigFlowSchema } from '../../services/integrationService';
import { Portal } from '../ui/Portal';
import { usePatientStore } from '../../store/slices/patientSlice';

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

// Fields whose value is a key-value map (rendered as dynamic rows).
function isKeyValueField(prop: any): boolean {
  return prop?.type === 'object' && prop?.['x-format'] === 'key-value';
}

// Password-like fields.
function isPasswordField(prop: any): boolean {
  return prop?.format === 'password' || prop?.['x-secret'] === true;
}

// Generic conditional visibility: a property is shown if the referenced
// field equals (or is contained in) the expected value. Supports both
// `depends_on` (single condition) and `depends_on_any` (list of conditions).
function isFieldVisible(
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

const ConfigFlowModal: React.FC<Props> = ({ domain, integrationId, onClose, onSuccess, initialData }) => {
  const { currentPatient } = usePatientStore();
  const [schema, setSchema] = useState<ConfigFlowSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});

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
  }, [domain, initialData]);

  const handleChange = (key: string, value: any, type: string) => {
    let parsedValue = value;
    if (type === 'integer') parsedValue = parseInt(value, 10);
    if (type === 'number') parsedValue = parseFloat(value);
    setFormData(prev => ({ ...prev, [key]: parsedValue }));
  };

  const handleJsonChange = (key: string, value: string) => {
    setFormData(prev => ({ ...prev, [key]: value }));
    try {
      JSON.parse(value);
      setJsonErrors(prev => ({ ...prev, [key]: '' }));
    } catch (e: any) {
      setJsonErrors(prev => ({ ...prev, [key]: 'Invalid JSON: ' + e.message }));
    }
  };

  // String-array chip editor helpers.
  const handleArrayAdd = (key: string, value: string) => {
    if (!value.trim()) return;
    setFormData(prev => ({ ...prev, [key]: [...(prev[key] || []), value.trim()] }));
  };
  const handleArrayRemove = (key: string, idx: number) => {
    setFormData(prev => ({ ...prev, [key]: (prev[key] || []).filter((_: any, i: number) => i !== idx) }));
  };

  // Key-value map editor helpers.
  const handleKvChange = (key: string, k: string, newK: string) => {
    setFormData(prev => {
      const obj = { ...(prev[key] || {}) };
      const v = obj[k];
      delete obj[k];
      obj[newK] = v;
      return { ...prev, [key]: obj };
    });
  };
  const handleKvValueChange = (key: string, k: string, v: string) => {
    setFormData(prev => ({ ...prev, [key]: { ...(prev[key] || {}), [k]: v } }));
  };
  const handleKvAdd = (key: string) => {
    setFormData(prev => ({ ...prev, [key]: { ...(prev[key] || {}), '': '' } }));
  };
  const handleKvRemove = (key: string, k: string) => {
    setFormData(prev => {
      const obj = { ...(prev[key] || {}) };
      delete obj[k];
      return { ...prev, [key]: obj };
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPatient) return;
    try {
      setSubmitting(true);
      await integrationService.submitConfigFlow(domain, currentPatient.id, formData, integrationId);
      toast.success("Integration configured successfully!");
      onSuccess();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to save configuration");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Portal>
      <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
        <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh] z-[10000]">
        <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
            {schema?.title || 'Configure Integration'}
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto">
          {loading ? (
            <div className="py-8 text-center text-gray-500 dark:text-dark-muted">Loading configuration UI...</div>
          ) : error ? (
            <div className="py-4 text-red-600 bg-red-50 dark:bg-red-900/20 rounded-xl px-4">{error}</div>
          ) : schema && (
            <form id="config-form" onSubmit={handleSubmit} className="space-y-4">
              {schema.description && <p className="text-sm text-gray-500 dark:text-dark-muted mb-4">{schema.description}</p>}

              {schema.data_schema?.properties && Object.entries(schema.data_schema.properties).map(([key, prop]: [string, any]) => {
                // Generic conditional visibility (generalizes the old
                // webhook-only `parser_type === 'custom'` special case).
                if (!isFieldVisible(key, prop, formData)) return null;
                const required = schema.data_schema.required?.includes(key);

                return (
                <div key={key}>
                  <label htmlFor={key} className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
                    {prop.title || key}
                    {required && <span className="text-red-500 ml-1">*</span>}
                  </label>
                  <div>
                    {prop.enum ? (
                      <div>
                        <select
                          id={key}
                          required={required}
                          value={formData[key] || prop.default || prop.enum[0]}
                          onChange={(e) => handleChange(key, e.target.value, prop.type)}
                          className="w-full rounded-xl border border-gray-200 dark:border-dark-border px-4 py-2 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                        >
                          {prop.enum.map((opt: string) => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                        {prop.enum_descriptions && prop.enum_descriptions[formData[key] || prop.default || prop.enum[0]] && (
                          <div className="mt-2 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-xl">
                            <p className="text-xs text-blue-700 dark:text-blue-300">
                              {prop.enum_descriptions[formData[key] || prop.default || prop.enum[0]]}
                            </p>
                          </div>
                        )}
                      </div>
                    ) : isKeyValueField(prop) ? (
                      <div className="space-y-2 rounded-xl border border-gray-200 dark:border-dark-border p-3 bg-gray-50 dark:bg-dark-bg">
                        {Object.entries(formData[key] || {}).map(([k, v]: [string, any], idx: number) => (
                          <div key={idx} className="flex items-center gap-2">
                            <input
                              type="text"
                              value={k}
                              placeholder="Key"
                              onChange={(e) => handleKvChange(key, k, e.target.value)}
                              className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                            />
                            <input
                              type={isPasswordField(prop) ? (showSecrets[key] ? 'text' : 'password') : 'text'}
                              value={v as string}
                              placeholder="Value"
                              onChange={(e) => handleKvValueChange(key, k, e.target.value)}
                              className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                            />
                            <button
                              type="button"
                              onClick={() => handleKvRemove(key, k)}
                              className="text-gray-400 hover:text-red-500 transition-colors"
                              title="Remove"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        ))}
                        <button
                          type="button"
                          onClick={() => handleKvAdd(key)}
                          className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400"
                        >
                          <Plus className="w-3.5 h-3.5" /> Add row
                        </button>
                      </div>
                    ) : isArrayField(prop) ? (
                      <div className="space-y-2 rounded-xl border border-gray-200 dark:border-dark-border p-3 bg-gray-50 dark:bg-dark-bg">
                        {(formData[key] || []).map((item: string, idx: number) => (
                          <div key={idx} className="flex items-center gap-2">
                            <input
                              type="text"
                              value={item}
                              onChange={(e) => {
                                const next = [...(formData[key] || [])];
                                next[idx] = e.target.value;
                                setFormData(prev => ({ ...prev, [key]: next }));
                              }}
                              className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                            />
                            <button
                              type="button"
                              onClick={() => handleArrayRemove(key, idx)}
                              className="text-gray-400 hover:text-red-500 transition-colors"
                              title="Remove"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        ))}
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            placeholder="Add value + Enter"
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                handleArrayAdd(key, (e.target as HTMLInputElement).value);
                                (e.target as HTMLInputElement).value = '';
                              }
                            }}
                            className="flex-1 rounded-lg border border-gray-200 dark:border-dark-border px-3 py-1.5 bg-white dark:bg-dark-surface text-sm text-gray-900 dark:text-dark-text"
                          />
                          <Plus className="w-4 h-4 text-gray-400" />
                        </div>
                      </div>
                    ) : prop.format === 'json' ? (
                      <div className="space-y-2">
                        <textarea
                          id={key}
                          required={required}
                          value={formData[key] !== undefined ? formData[key] : ''}
                          onChange={(e) => handleJsonChange(key, e.target.value)}
                          rows={10}
                          className={`w-full rounded-xl border px-4 py-2 bg-gray-50 dark:bg-dark-bg text-gray-900 dark:text-dark-text font-mono text-sm focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors ${jsonErrors[key] ? 'border-red-500' : 'border-gray-200 dark:border-dark-border'}`}
                        />
                        {jsonErrors[key] && <p className="text-xs text-red-500 font-medium">{jsonErrors[key]}</p>}
                      </div>
                    ) : isPasswordField(prop) ? (
                      <div className="relative">
                        <input
                          type={showSecrets[key] ? 'text' : 'password'}
                          id={key}
                          required={required}
                          value={formData[key] !== undefined ? formData[key] : ''}
                          onChange={(e) => handleChange(key, e.target.value, prop.type)}
                          className="w-full rounded-xl border border-gray-200 dark:border-dark-border px-4 py-2 pr-10 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                          placeholder={initialData?.[key] === '***' ? '•••••••• (re-enter to change)' : ''}
                          autoComplete="off"
                        />
                        <button
                          type="button"
                          onClick={() => setShowSecrets(prev => ({ ...prev, [key]: !prev[key] }))}
                          className="absolute inset-y-0 right-0 px-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
                          title={showSecrets[key] ? 'Hide' : 'Show'}
                        >
                          {showSecrets[key] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                      </div>
                    ) : prop.type === 'boolean' ? (
                      <input
                        type="checkbox"
                        id={key}
                        checked={formData[key] || false}
                        onChange={(e) => handleChange(key, e.target.checked, prop.type)}
                        className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                      />
                    ) : (
                      <input
                        type={prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'}
                        id={key}
                        required={required}
                        value={formData[key] !== undefined ? formData[key] : ''}
                        onChange={(e) => handleChange(key, e.target.value, prop.type)}
                        min={prop.minimum}
                        max={prop.maximum}
                        className="w-full rounded-xl border border-gray-200 dark:border-dark-border px-4 py-2 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                      />
                    )}
                  </div>
                  {prop.description && <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">{prop.description}</p>}
                </div>
                );
              })}
            </form>
          )}
        </div>

        <div className="p-6 bg-gray-50 dark:bg-dark-border/30 flex items-center justify-end space-x-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-600 dark:text-dark-muted hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            form="config-form"
            disabled={loading || !!error || submitting || Object.values(jsonErrors).some(err => err !== '')}
            className="px-6 py-2 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-all shadow-md shadow-blue-200/50 dark:shadow-none active:scale-95 disabled:opacity-50"
          >
            {submitting ? 'Saving...' : 'Save Configuration'}
          </button>
        </div>
      </div>
      </div>
    </Portal>
  );
};

export default ConfigFlowModal;
