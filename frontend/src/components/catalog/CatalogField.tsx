/**
 * CatalogField — a form-field wrapper around {@link CatalogItemPicker}.
 *
 * The standard input-vs-field split: `CatalogItemPicker` is the raw controlled
 * input (`value`/`onChange`/`allowedTypes`/…), usable in non-form contexts
 * (the relations editor, AI tools). `CatalogField` adds the form dressing a
 * metadata form needs — label, required asterisk, disabled state, column
 * span — so `DynamicMetadataForm` (and any other form that needs a catalog
 * pick) can render a pick consistently without reimplementing the label/error
 * chrome each time.
 *
 * No business logic: every picker prop passes through unchanged.
 */
import React from 'react';
import { CatalogItemPicker } from './CatalogItemPicker';
import type { CatalogItemPickerProps } from './CatalogItemPicker';

export interface CatalogFieldProps
  extends Omit<CatalogItemPickerProps, 'value' | 'onChange'> {
  label: string;
  /** When true, renders a `*` next to the label. */
  required?: boolean;
  /** Controlled value (same shape as CatalogItemPicker.value). */
  value: CatalogItemPickerProps['value'];
  onChange: CatalogItemPickerProps['onChange'];
  /** Optional error text rendered under the input. */
  error?: string;
  /** Wrapper className for layout (e.g. column span). */
  className?: string;
}

export const CatalogField: React.FC<CatalogFieldProps> = ({
  label,
  required,
  error,
  className = '',
  ...pickerProps
}) => {
  return (
    <div className={className}>
      <label className="block text-[10px] font-bold uppercase tracking-widest mb-1.5 ml-1 opacity-60">
        {label} {required && <span className="text-red-500">*</span>}
      </label>
      <CatalogItemPicker block {...pickerProps} />
      {error && (
        <p className="mt-1 ml-1 text-[11px] text-red-500 font-medium">{error}</p>
      )}
    </div>
  );
};
