import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldOff } from 'lucide-react';
import { Button } from '@neuronection/assistant-ui';
import { PageHeader } from '../../components/ui/PageHeader';

/**
 * The guarded-route landing state (ADR 0054): the UI knows the signed-in
 * user cannot use this surface, so it shows a clear message instead of a
 * guaranteed 403 from the central gate. Server enforcement is unchanged —
 * this page never unlocks anything.
 */
function NoAccessPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('no_access.title', 'No access')}
        subtitle={t('no_access.description', 'Your account does not have permission to use this page.')}
        icon={<ShieldOff className="w-8 h-8" />}
      />
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-10 flex flex-col items-center text-center gap-4">
        <p className="text-sm text-gray-500 dark:text-dark-muted max-w-md">
          {from
            ? t('no_access.page_hint', 'The page {{page}} requires permissions your role does not include.', { page: from })
            : t('no_access.description', 'Your account does not have permission to use this page.')}
        </p>
        <Link to="/dashboard">
          <Button>{t('no_access.back', 'Back to dashboard')}</Button>
        </Link>
      </div>
    </div>
  );
}

export default NoAccessPage;
