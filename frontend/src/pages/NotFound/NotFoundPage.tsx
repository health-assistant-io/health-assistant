import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Compass } from 'lucide-react';
import { Button } from '@neuronection/assistant-ui';
import { PageHeader } from '../../components/ui/PageHeader';

function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('not_found.title', 'Page not found')}
        subtitle={t('not_found.description', 'The page you are looking for does not exist or has moved.')}
        icon={<Compass className="w-8 h-8" />}
      />
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-xs border border-gray-100 dark:border-dark-border p-10 flex flex-col items-center text-center gap-4">
        <p className="text-6xl font-black text-gray-200 dark:text-dark-border">404</p>
        <p className="text-sm text-gray-500 dark:text-dark-muted max-w-md">
          {t('not_found.description', 'The page you are looking for does not exist or has moved.')}
        </p>
        <Link to="/dashboard">
          <Button>{t('not_found.back', 'Back to dashboard')}</Button>
        </Link>
      </div>
    </div>
  );
}

export default NotFoundPage;
