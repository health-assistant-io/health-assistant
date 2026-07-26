import React from 'react';
import { Loader2, CheckCircle2 } from 'lucide-react';
import type { Patient } from '../../../types/patient';

/** Common props every inline-form section receives. */
export interface SectionProps {
  patient: Patient;
  /** Called after a successful save — the parent re-polls the checklist. */
  onSaved?: () => void;
  /** When navigated to from a specific step, the field id to highlight. */
  activeField?: string;
}

/** Shared header for an inline-form section. */
export const SectionHeader: React.FC<{ title: string; description?: string; optional?: boolean }> = ({
  title,
  description,
  optional,
}) => (
  <div className="mb-5">
    <div className="flex items-center gap-2">
      <h3 className="text-lg font-bold text-brand-navy dark:text-dark-text">{title}</h3>
      {optional && (
        <span className="text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 dark:bg-dark-border dark:text-dark-muted">
          optional
        </span>
      )}
    </div>
    {description && (
      <p className="mt-1 text-sm text-gray-500 dark:text-dark-muted">{description}</p>
    )}
  </div>
);

/** A labeled field wrapper with consistent wizard styling. */
export const SetupField: React.FC<{
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
}> = ({ label, hint, htmlFor, children }) => (
  <div>
    <label htmlFor={htmlFor} className="block text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">
      {label}
    </label>
    {children}
    {hint && <p className="mt-1 text-[11px] text-gray-400 dark:text-dark-muted/70">{hint}</p>}
  </div>
);

const inputCls =
  'w-full rounded-xl border border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-bg px-3 py-2.5 text-sm text-gray-800 dark:text-dark-text focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all';

export const SetupInput = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  (props, ref) => <input ref={ref} {...props} className={`${inputCls} ${props.className ?? ''}`} />,
);
SetupInput.displayName = 'SetupInput';

export const SetupSelect: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = (props) => (
  <select {...props} className={`${inputCls} ${props.className ?? ''}`} />
);

/** Save button + status row, shared by all sections. */
export const SaveBar: React.FC<{
  onSave: () => void;
  saving?: boolean;
  saved?: boolean;
  disabled?: boolean;
  label?: string;
}> = ({ onSave, saving, saved, disabled, label = 'Save' }) => (
  <div className="flex items-center gap-3 mt-6">
    <button
      type="button"
      onClick={onSave}
      disabled={saving || disabled}
      className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl font-semibold text-sm hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {saving && <Loader2 className="w-4 h-4 animate-spin" />}
      {label}
    </button>
    {saved && !saving && (
      <span className="inline-flex items-center gap-1 text-sm text-green-600 dark:text-green-400">
        <CheckCircle2 className="w-4 h-4" /> Saved
      </span>
    )}
  </div>
);
