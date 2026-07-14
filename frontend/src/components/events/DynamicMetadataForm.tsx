import React from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Calendar, Info, MapPin, Zap, Target } from 'lucide-react';
import { DatePicker } from '../ui/DatePicker';
import { ScaleSlider } from '../ui/ScaleSlider';
import { CatalogField } from '../catalog/CatalogField';
import type { CatalogSelection } from '../../types/catalog';
import type {
  CatalogFieldValue,
  MetadataField,
  MetadataSchema,
} from '../../types/metadataSchema';

interface Props {
  schema: MetadataSchema | null | undefined;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  color?: string;
}

/**
 * Renders the typed ``MetadataSchema`` declared on a ``ClinicalEventType``.
 *
 * The switch over ``field.type`` is exhaustive: every ``MetadataFieldType``
 * literal has a branch, and the ``default`` arm asserts ``never`` so adding a
 * new field type without a renderer is a compile error (TS catches it at
 * build time). This permanently closes the legacy bug class where
 * ``select``/``code`` silently rendered nothing.
 *
 * ``catalog-select`` fields render through the reusable ``CatalogField`` →
 * ``CatalogItemPicker`` pipeline, restricted to the declared ``catalogs``
 * (and optionally narrowed by ``concept_kind``). The stored value is a
 * ``{type,id,label}`` object (single) or an array of them (multi).
 */
export const DynamicMetadataForm: React.FC<Props> = ({ schema, value, onChange, color = '#3b82f6' }) => {
  const { t } = useTranslation();

  if (!schema || !Array.isArray(schema.fields) || schema.fields.length === 0) {
    return null;
  }

  const handleChange = (fieldName: string, fieldValue: unknown) => {
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
        {schema.fields.map((field: MetadataField) => (
          <FieldRenderer
            key={field.name}
            field={field}
            value={value[field.name]}
            onChange={(v) => handleChange(field.name, v)}
            color={color}
            renderIcon={renderIcon}
          />
        ))}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Per-field renderer — exhaustive over MetadataFieldType.
// ---------------------------------------------------------------------------

interface FieldRendererProps {
  field: MetadataField;
  value: unknown;
  onChange: (v: unknown) => void;
  color: string;
  renderIcon: (name: string) => React.ReactNode;
}

const FieldRenderer: React.FC<FieldRendererProps> = ({
  field,
  value,
  onChange,
  color,
  renderIcon,
}) => {
  switch (field.type) {
    case 'text':
      return (
        <div className="md:col-span-2">
          <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
            {field.label} {field.required && '*'}
          </label>
          <div className="relative">
            <div className="absolute left-4 top-1/2 -translate-y-1/2 opacity-40">
              {renderIcon(field.name)}
            </div>
            <input
              type="text"
              required={field.required}
              placeholder={field.placeholder ?? `Enter ${field.label.toLowerCase()}...`}
              className="w-full pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all placeholder:font-normal placeholder:opacity-50"
              style={{ '--tw-ring-color': `${color}40` } as React.CSSProperties}
              value={(value as string) || ''}
              onChange={(e) => onChange(e.target.value)}
            />
          </div>
        </div>
      );

    case 'number': {
      // A number with both min and max is naturally a scale/intensity → render
      // the gradient slider + synced input. Unbounded numbers stay plain.
      const isScale = field.min !== undefined && field.max !== undefined;
      if (isScale) {
        return (
          <div>
            <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
              {field.label} {field.required && '*'}
            </label>
            <div className="px-1 py-2.5 bg-white dark:bg-dark-surface rounded-xl border border-transparent">
              <ScaleSlider
                value={value as number | '' | undefined}
                onChange={(v) => onChange(v)}
                min={field.min as number}
                max={field.max as number}
              />
            </div>
          </div>
        );
      }
      return (
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
            {field.label} {field.required && '*'}
          </label>
          <div className="relative">
            <div className="absolute left-4 top-1/2 -translate-y-1/2 opacity-40">
              {renderIcon(field.name)}
            </div>
            <input
              type="number"
              min={field.min}
              max={field.max}
              required={field.required}
              className="w-full pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all"
              style={{ '--tw-ring-color': `${color}40` } as React.CSSProperties}
              value={value === undefined || value === null ? '' : String(value)}
              onChange={(e) =>
                onChange(e.target.value === '' ? '' : Number(e.target.value))
              }
            />
          </div>
        </div>
      );
    }

    case 'date':
      return (
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
            {field.label} {field.required && '*'}
          </label>
          <div className="w-full relative">
            <div className="absolute left-4 top-1/2 -translate-y-1/2 opacity-40 z-10">
              {renderIcon(field.name)}
            </div>
            <DatePicker
              required={field.required}
              className="pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border border-transparent rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 outline-none font-medium transition-all"
              value={(value as string) || ''}
              onChange={(date) => onChange(date)}
            />
          </div>
        </div>
      );

    case 'boolean': {
      const boolValue = Boolean(value);
      return (
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
            {field.label} {field.required && '*'}
          </label>
          <div
            onClick={() => onChange(!boolValue)}
            className={`flex items-center justify-between pl-11 pr-4 py-3 bg-white dark:bg-dark-surface border rounded-xl cursor-pointer transition-all ${boolValue ? 'border-transparent ring-2' : 'border-transparent'}`}
            style={{ '--tw-ring-color': `${color}40` } as React.CSSProperties}
          >
            <span className="text-xs font-bold opacity-60 uppercase">{field.label}</span>
            <div
              className={`w-10 h-5 rounded-full relative transition-colors ${boolValue ? '' : 'bg-gray-200 dark:bg-dark-bg'}`}
              style={{ backgroundColor: boolValue ? color : undefined }}
            >
              <div className={`absolute top-1 w-3 h-3 bg-white rounded-full transition-all ${boolValue ? 'right-1' : 'left-1'}`} />
            </div>
          </div>
        </div>
      );
    }

    case 'catalog-select': {
      // The stored value is {type,id,label} (single) or an array (multi).
      // CatalogItemPicker always works with CatalogSelection[].
      const multi = field.multi ?? false;
      const raw = value as CatalogFieldValue | CatalogFieldValue[] | undefined;
      const pickerValue: CatalogSelection[] = (() => {
        if (!raw) return [];
        if (Array.isArray(raw)) return raw as CatalogSelection[];
        return [raw as CatalogSelection];
      })();
      return (
        <CatalogField
          label={field.label}
          required={field.required}
          allowedTypes={field.catalogs}
          conceptKind={field.concept_kind}
          mode={multi ? 'multi' : 'single'}
          value={pickerValue}
          onChange={(next) => onChange(multi ? next : (next[0] ?? null))}
          className={field.catalogs && field.catalogs.length > 1 ? 'md:col-span-2' : ''}
        />
      );
    }

    default: {
      // Exhaustiveness guard: if MetadataFieldType gains a new literal
      // without a branch above, this line fails to compile.
      const _exhaustive: never = field.type;
      void _exhaustive;
      return null;
    }
  }
};
