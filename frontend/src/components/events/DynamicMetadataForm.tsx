import React from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Calendar, Info, MapPin, Zap, Target } from 'lucide-react';
import { BodyPartSelector } from '../ui/BodyPartSelector';

interface Field {
  name: string;
  label: string;
  type: 'text' | 'number' | 'date' | 'boolean' | 'select' | 'code' | 'creatable-select';
  source?: string;
  required?: boolean;
  min?: number;
  max?: number;
  options?: { label: string; value: any }[];
}

interface Props {
  schema: { fields: Field[] } | any;
  value: Record<string, any>;
  onChange: (value: Record<string, any>) => void;
  color?: string;
}

export const DynamicMetadataForm: React.FC<Props> = ({ schema, value, onChange, color = '#3b82f6' }) => {
  const { t } = useTranslation();
  
  if (!schema || !schema.fields || !Array.isArray(schema.fields)) {
    return null;
  }

  const handleChange = (fieldName: string, fieldValue: any) => {
    onChange({ ...value, [fieldName]: fieldValue });
  };

  const renderIcon = (fieldName: string) => {
    const name = fieldName.toLowerCase();
    if (name.includes('date')) return <Calendar className="w-4 h-4" />;
    if (name.includes('intensity') || name.includes('score')) return <Activity className="w-4 h-4" />;
    if (name.includes('body') || name.includes('part') || name.includes('anatomy')) return <Target className="w-4 h-4" />;
    if (name.includes('location') || name.includes('area')) return <MapPin className="w-4 h-4" />;
    if (name.includes('trigger') || name.includes('mechanism')) return <Zap className="w-4 h-4" />;
    return <Info className="w-4 h-4" />;
  };

  return (
    <div className="rounded-3xl p-6 border transition-all duration-300" style={{ backgroundColor: `${color}05`, borderColor: `${color}20` }}>
      <div className="flex items-center space-x-3 mb-6">
        <div className="p-2 rounded-xl" style={{ backgroundColor: `${color}20`, color: color }}>
          <Zap className="w-5 h-5" />
        </div>
        <h3 className="text-sm font-bold uppercase tracking-widest" style={{ color: color }}>
          {t('events.specialized_details')}
        </h3>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {schema.fields.map((field: Field) => (
          <div key={field.name} className={field.type === 'text' ? 'md:col-span-2' : ''}>
            <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
              {field.label} {field.required && '*'}
            </label>
            
            <div className="relative">
              {field.type !== 'creatable-select' && (
                <div className="absolute left-4 top-1/2 -translate-y-1/2 opacity-40">
                  {renderIcon(field.name)}
                </div>
              )}

              {field.type === 'text' && (
                <input
                  type="text"
                  required={field.required}
                  placeholder={`Enter ${field.label.toLowerCase()}...`}
                  className="w-full pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all"
                  style={{ '--tw-ring-color': `${color}40` } as any}
                  value={value[field.name] || ''}
                  onChange={e => handleChange(field.name, e.target.value)}
                />
              )}

              {field.type === 'number' && (
                <div className="flex items-center space-x-3">
                   <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    required={field.required}
                    className="flex-1 pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all"
                    style={{ '--tw-ring-color': `${color}40` } as any}
                    value={value[field.name] || ''}
                    onChange={e => handleChange(field.name, e.target.value === '' ? '' : Number(e.target.value))}
                  />
                  {field.min !== undefined && field.max !== undefined && (
                    <div className="flex items-center space-x-2 bg-white dark:bg-dark-surface px-3 py-3 rounded-xl border border-transparent">
                       <span className="text-[10px] font-bold opacity-40 uppercase">Scale</span>
                       <span className="text-xs font-bold" style={{ color: color }}>{value[field.name] || 0} / {field.max}</span>
                    </div>
                  )}
                </div>
              )}

              {field.type === 'date' && (
                <input
                  type="date"
                  required={field.required}
                  className="w-full pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all"
                  style={{ '--tw-ring-color': `${color}40` } as any}
                  value={value[field.name] || ''}
                  onChange={e => handleChange(field.name, e.target.value)}
                />
              )}

              {field.type === 'creatable-select' && field.source === 'body-parts' && (
                <BodyPartSelector
                  selectedId={value[field.name]}
                  onSelect={(part) => handleChange(field.name, part.id)}
                  placeholder={`Select ${field.label}...`}
                />
              )}

              {field.type === 'boolean' && (
                <div 
                  onClick={() => handleChange(field.name, !value[field.name])}
                  className={`flex items-center justify-between pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border rounded-xl cursor-pointer transition-all ${value[field.name] ? 'border-transparent ring-2' : 'border-transparent'}`}
                  style={{ '--tw-ring-color': `${color}40` } as any}
                >
                  <span className="text-xs font-bold opacity-60 uppercase">{field.label}</span>
                  <div className={`w-10 h-5 rounded-full relative transition-colors ${value[field.name] ? '' : 'bg-gray-200 dark:bg-dark-bg'}`} style={{ backgroundColor: value[field.name] ? color : undefined }}>
                    <div className={`absolute top-1 w-3 h-3 bg-white rounded-full transition-all ${value[field.name] ? 'right-1' : 'left-1'}`} />
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
