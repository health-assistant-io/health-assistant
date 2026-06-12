import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { toast } from 'react-toastify';
import { integrationService, ConfigFlowSchema } from '../../services/integrationService';
import { Portal } from '../ui/Portal';
import { usePatientStore } from '../../store/slices/patientSlice';

interface Props {
  domain: string;
  onClose: () => void;
  onSuccess: () => void;
  initialData?: Record<string, any>;
}

const ConfigFlowModal: React.FC<Props> = ({ domain, onClose, onSuccess, initialData }) => {
  const { currentPatient } = usePatientStore();
  const [schema, setSchema] = useState<ConfigFlowSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSchema = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await integrationService.getConfigFlow(domain);
        setSchema(data);
        
        // Initialize form data with defaults or initialData
        if (data.data_schema?.properties) {
          const initial: Record<string, any> = { ...initialData };
          Object.entries(data.data_schema.properties).forEach(([key, value]: [string, any]) => {
            if (initial[key] === undefined && value.default !== undefined) {
              initial[key] = value.default;
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
  }, [domain]);

  const handleChange = (key: string, value: any, type: string) => {
    let parsedValue = value;
    if (type === 'integer') parsedValue = parseInt(value, 10);
    if (type === 'number') parsedValue = parseFloat(value);
    
    setFormData(prev => ({ ...prev, [key]: parsedValue }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPatient) return;
    try {
      setSubmitting(true);
      await integrationService.submitConfigFlow(domain, currentPatient.id, formData);
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
      <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
        <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh]">
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
              
              {schema.data_schema?.properties && Object.entries(schema.data_schema.properties).map(([key, prop]: [string, any]) => (
                <div key={key}>
                  <label htmlFor={key} className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
                    {prop.title || key}
                    {schema.data_schema.required?.includes(key) && <span className="text-red-500 ml-1">*</span>}
                  </label>
                  <div>
                    {prop.type === 'boolean' ? (
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
                        required={schema.data_schema.required?.includes(key)}
                        value={formData[key] !== undefined ? formData[key] : ''}
                        onChange={(e) => handleChange(key, e.target.value, prop.type)}
                        min={prop.minimum}
                        max={prop.maximum}
                        className="w-full rounded-xl border border-gray-200 dark:border-dark-border px-4 py-2 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                      />
                    )}
                  </div>
                </div>
              ))}
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
            disabled={loading || !!error || submitting}
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
