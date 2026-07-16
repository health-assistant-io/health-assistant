/**
 * CopyButton — a tiny icon button that copies a value to the clipboard and
 * shows a brief "copied" confirmation (check icon) + a toast.
 *
 * Used by {@link CodeBadge} and {@link KeyValueGrid} (copyable rows), and
 * standalone for "Copy JSON" / copy-UUID affordances. Falls back gracefully
 * when the async Clipboard API is unavailable (older browsers / insecure
 * contexts): the button stays clickable but reports the failure via toast.
 */
import React, { useCallback, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { toast } from 'react-toastify';
import { useTranslation } from 'react-i18next';

interface CopyButtonProps {
  value: string;
  /** Accessible label / tooltip (i18n key resolved by caller). */
  label?: string;
  /** Shown in the success toast; defaults to a generic "Copied". */
  toastLabel?: string;
  className?: string;
  /** Icon pixel size (default 14). */
  size?: number;
  /** Hide the button entirely when `value` is empty (default true). */
  hideWhenEmpty?: boolean;
}

export const CopyButton: React.FC<CopyButtonProps> = ({
  value,
  label,
  toastLabel,
  className = '',
  size = 14,
  hideWhenEmpty = true,
}) => {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onClick = useCallback(
    async (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!value) return;
      try {
        if (navigator?.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
        } else {
          // Legacy fallback for non-secure contexts.
          const ta = document.createElement('textarea');
          ta.value = value;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1500);
        toast.success(
          toastLabel ?? t('catalogs.copied', 'Copied'),
          { autoClose: 1200 },
        );
      } catch {
        toast.error(t('catalogs.copy_failed', 'Copy failed'));
      }
    },
    [value, toastLabel, t],
  );

  if (hideWhenEmpty && !value) return null;

  const Icon = copied ? Check : Copy;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label ?? t('catalogs.copy', 'Copy')}
      title={label ?? t('catalogs.copy', 'Copy')}
      className={`inline-flex items-center justify-center text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded ${className}`}
    >
      <Icon style={{ width: size, height: size }} aria-hidden />
    </button>
  );
};

export default CopyButton;
