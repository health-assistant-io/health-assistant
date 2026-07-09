/**
 * Shared layout primitives for the catalog item forms — a labeled field wrapper
 * + a text input + a textarea. Keeps the per-type forms consistent and terse.
 */
import React from 'react';

export const Field: React.FC<{
  label: string;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, hint, children }) => (
  <div>
    <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
      {label}
    </label>
    {children}
    {hint && <p className="mt-1 text-[11px] text-gray-400">{hint}</p>}
  </div>
);

const inputCls =
  'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none';

export const TextInput: React.FC<
  React.InputHTMLAttributes<HTMLInputElement>
> = (props) => <input {...props} className={`${inputCls} ${props.className ?? ''}`} />;

export const TextArea: React.FC<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
> = (props) => (
  <textarea {...props} className={`${inputCls} ${props.className ?? ''}`} />
);
