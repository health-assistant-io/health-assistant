/**
 * One-time TOTP enrollment panel (plan 16 H5): QR from the otpauth://
 * URI (rendered locally — the secret never leaves the client-server
 * pair), the base32 secret as copyable text, and the single-use
 * recovery codes with copy-all.
 */
import { useTranslation } from 'react-i18next';
import { QRCodeSVG } from 'qrcode.react';
import { KeyRound, ShieldAlert } from 'lucide-react';
import { CopyButton } from '../ui/CopyButton';
import type { MFAEnrollment } from '../../services/mfaService';

export function MFAEnrollmentPanel({ enrollment }: { enrollment: MFAEnrollment }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row items-center gap-5">
        <div className="p-3 bg-white rounded-2xl border border-gray-100 dark:border-dark-border shrink-0">
          <QRCodeSVG value={enrollment.uri} size={148} level="M" />
        </div>
        <div className="space-y-2 min-w-0 flex-1">
          <p className="text-xs font-black uppercase tracking-widest text-gray-400">
            {t('mfa.secret_label', 'Setup key (manual entry)')}
          </p>
          <div className="flex items-center gap-2 min-w-0">
            <code className="flex-1 min-w-0 px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-xs font-mono break-all dark:text-dark-text">
              {enrollment.secret}
            </code>
            <CopyButton value={enrollment.secret} label={t('mfa.copy', 'Copy')} />
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <code className="flex-1 min-w-0 px-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-100 dark:border-dark-border rounded-xl text-[10px] font-mono break-all dark:text-dark-text">
              {enrollment.uri}
            </code>
            <CopyButton value={enrollment.uri} label={t('mfa.copy_uri', 'Copy URI')} />
          </div>
        </div>
      </div>

      <div className="p-4 rounded-2xl border border-amber-200 dark:border-amber-900/40 bg-amber-50/60 dark:bg-amber-900/10 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            <KeyRound className="w-4 h-4 text-amber-600" />
            <p className="text-sm font-bold text-amber-800 dark:text-amber-300">
              {t('mfa.recovery_title', 'Recovery codes')}
            </p>
          </div>
          <CopyButton value={enrollment.recovery_codes.join('\n')} label={t('mfa.copy_all', 'Copy all')} />
        </div>
        <p className="text-xs text-amber-700 dark:text-amber-400">
          <ShieldAlert className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
          {t(
            'mfa.recovery_help',
            'Single-use codes for when you lose your authenticator. Shown only once — store them now.'
          )}
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {enrollment.recovery_codes.map((code) => (
            <code
              key={code}
              className="px-2 py-1.5 bg-white dark:bg-dark-surface border border-amber-200 dark:border-amber-900/40 rounded-lg text-center text-xs font-mono font-bold text-amber-900 dark:text-amber-200"
            >
              {code}
            </code>
          ))}
        </div>
      </div>
    </div>
  );
}

export default MFAEnrollmentPanel;
