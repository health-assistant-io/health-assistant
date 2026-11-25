import React from 'react';
import { Coins } from 'lucide-react';
import { DataCostTab } from '../../components/settings/DataCostTab';
import { PageHeader } from '../../components/ui/PageHeader';
import { useAuthStore } from '../../store/slices/authSlice';
import { useTranslation } from 'react-i18next';

interface DataCostProps {
  scope?: 'global' | 'tenant';
}

/** Tenant/system AI usage ledger + price editor (plan 58 P2). */
export const DataCost: React.FC<DataCostProps> = ({ scope = 'tenant' }) => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const tenantId = user?.tenant_id;

  return (
    <div className="space-y-6">
      <PageHeader
        title={scope === 'global' ? 'System Data & Cost' : 'Data & Cost'}
        subtitle="AI call spend, unpriced calls and the prices used at call time"
        icon={<Coins className="w-8 h-8" />}
      />
      {tenantId ? (
        <div className="bg-white dark:bg-dark-surface rounded-lg shadow-sm p-6 mb-20">
          <DataCostTab tenantId={tenantId} />
        </div>
      ) : (
        <p className="text-sm text-gray-500">{t('settings.cost_select_tenant')}</p>
      )}
    </div>
  );
};
