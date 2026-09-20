import { useTranslation } from 'react-i18next';
import { setupErrorDetail, type ProviderSetupErrorDetail } from '../../../api/aiConfig';

const ERROR_CODES = [
  'invalid_key',
  'insufficient_credit',
  'new_user_quota',
  'region_unavailable',
  'timeout',
  'local_not_running',
  'unknown',
] as const;

export function errorLabel(code: string): string {
  return (ERROR_CODES as readonly string[]).includes(code) ? code : 'unknown';
}

export { setupErrorDetail };

export function SetupErrorPanel({
  error,
  plainError,
}: {
  error: ProviderSetupErrorDetail | null;
  plainError: string | null;
}) {
  const { t } = useTranslation();
  if (error) {
    return (
      <div
        role="alert"
        className="space-y-1 rounded-lg border border-dashed border-red-300 px-3 py-2 text-xs text-red-600 dark:border-red-800 dark:text-red-400"
      >
        <p>{t(`settings.ai.setup.errors.${errorLabel(error.code)}`)}</p>
        {error.suspected_vendor ? (
          <p>
            {t('settings.ai.setup.errors.suspected_vendor', {
              vendor: error.suspected_vendor,
            })}
          </p>
        ) : null}
        {error.message ? (
          <p className="break-words text-gray-500 dark:text-dark-muted">{error.message}</p>
        ) : null}
      </div>
    );
  }
  if (plainError) {
    return (
      <p role="alert" className="text-xs text-red-600 dark:text-red-400">
        {plainError}
      </p>
    );
  }
  return null;
}
