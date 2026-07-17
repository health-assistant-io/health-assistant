/**
 * InstanceField — a form-field wrapper around {@link InstancePicker}.
 *
 * The standard input-vs-field split (mirrors `CatalogField`): `InstancePicker`
 * is the raw controlled input (`value`/`onChange`/`allowedTypes`/…), usable in
 * non-form contexts; `InstanceField` adds the form dressing a form needs —
 * label, required asterisk, error text, wrapper layout — so any form that needs
 * a record pick can render it consistently without reimplementing the chrome.
 *
 * No business logic: every picker prop passes through unchanged.
 */
import React from 'react';
import { InstancePicker } from './InstancePicker';
import type { InstancePickerProps } from './InstancePicker';

export interface InstanceFieldProps
  extends Omit<InstancePickerProps, 'value' | 'onChange'> {
  label: string;
  /** When true, renders a `*` next to the label. */
  required?: boolean;
  /** Controlled value (same shape as InstancePicker.value). */
  value: InstancePickerProps['value'];
  onChange: InstancePickerProps['onChange'];
  /** Optional error text rendered under the input. */
  error?: string;
  /** Wrapper className for layout (e.g. column span). */
  className?: string;
}

export const InstanceField: React.FC<InstanceFieldProps> = ({
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
      <InstancePicker block {...pickerProps} />
      {error && (
        <p className="mt-1 ml-1 text-[11px] text-red-500 font-medium">{error}</p>
      )}
    </div>
  );
};
