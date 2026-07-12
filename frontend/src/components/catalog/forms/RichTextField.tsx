/**
 * RichTextField — a labeled catalog-form field wrapping the compact
 * {@link RichTextEditor}. Use for paragraph-style fields (description,
 * indications, contraindications, info) across the catalog create/edit forms.
 *
 * The editor auto-converts stored Markdown → HTML on load (LLM-produced
 * content becomes editable), and always emits HTML on change.
 */
import React from 'react';
import { Field } from './FormFields';
import { RichTextEditor } from '../../ui/RichTextEditor';

interface RichTextFieldProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: string;
}

export const RichTextField: React.FC<RichTextFieldProps> = ({
  label,
  hint,
  value,
  onChange,
  placeholder,
  minHeight = '150px',
}) => (
  <Field label={label} hint={hint}>
    <RichTextEditor
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      minHeight={minHeight}
      compact
    />
  </Field>
);

export default RichTextField;
