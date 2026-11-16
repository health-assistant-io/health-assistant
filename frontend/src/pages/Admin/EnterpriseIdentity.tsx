import { useTranslation } from 'react-i18next';
import { Fingerprint } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { useAccessStore } from '../../store/slices/accessSlice';
import { SsoConfigCard } from '../../components/admin/SsoConfigCard';
import { ScimTokensCard } from '../../components/admin/ScimTokensCard';

/**
 * Tenant enterprise identity (program 12B UI): the tenant's OIDC
 * bindings and SCIM directory credentials. Reads are gated by
 * `settings:read`, writes by `settings:write` (SSO) and `tenant:manage`
 * (SCIM); the server re-asserts the tenant boundary and answers a hidden
 * 404 for a foreign tenant.
 */
function EnterpriseIdentity() {
  const { t } = useTranslation();
  const tenant = useAccessStore((s) => s.tenant);
  const can = useAccessStore((s) => s.can);

  return (
    <div className="space-y-8 pb-10">
      <PageHeader
        title={t('enterprise.title', 'Enterprise identity')}
        subtitle={t('enterprise.subtitle', 'Single sign-on and directory provisioning for this tenant.')}
        icon={<Fingerprint className="w-8 h-8" />}
      />

      {!tenant ? (
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('enterprise.no_tenant', 'No tenant context is available.')}
        </p>
      ) : (
        <>
          <SsoConfigCard tenantId={tenant.id} canWrite={can('settings:write')} />
          <ScimTokensCard tenantId={tenant.id} canWrite={can('tenant:manage')} />
        </>
      )}
    </div>
  );
}

export default EnterpriseIdentity;
