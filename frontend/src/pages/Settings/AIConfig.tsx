import React, { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { SegmentedTabs } from '@neuronection/assistant-ui';
import { ProviderManager } from '../../components/settings/ProviderManager';
import { ModelsTab } from '../../components/settings/ModelsTab';
import { TaskAssignment } from '../../components/settings/TaskAssignment';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { LoadingState } from '../../components/ui/LoadingState';
import { PageHeader } from '../../components/ui/PageHeader';
import { Cpu } from 'lucide-react';

type AITab = 'providers' | 'models' | 'tasks';

// The AI tab hosts exactly ai.* domains (ADR-0033): providers, models and
// task assignments. Agent behavior moved to ToolsConfig in plan 27 Phase 4;
// its deep link redirects. Exported for the settings-map conformance test.
export const AI_TABS: readonly AITab[] = ['providers', 'models', 'tasks'];

interface AIConfigProps {
  scope?: 'global' | 'tenant' | 'user';
  tenantId?: string;
  userId?: string;
  embedded?: boolean;
}

export const AIConfig: React.FC<AIConfigProps> = ({
  scope = 'user',
  tenantId,
  userId,
  embedded = false
}) => {
  const { loadConfigSummary, isLoading } = useAIConfigStore();
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabFromUrl = searchParams.get('tab');
  const [activeTab, setActiveTabState] = useState<AITab>(
    (tabFromUrl && (AI_TABS as readonly string[]).includes(tabFromUrl))
      ? (tabFromUrl as AITab)
      : 'providers',
  );

  // Keep the URL ?tab= in sync so the setup wizard can deep-link to a tab
  // and so the tab survives a page refresh.
  const setActiveTab = (tab: AITab) => {
    setActiveTabState(tab);
    setSearchParams(tab !== 'providers' ? { tab } : {}, { replace: true });
  };

  // If the URL ?tab changes externally (e.g. the wizard redirects to
  // ?tab=models while this page is already mounted), follow it.
  // Legacy deep link (INV-6): ?tab=agent moved to the tier's tools page.
  useEffect(() => {
    if (tabFromUrl === 'agent') {
      navigate(
        scope === 'global' ? '/admin/system/tools' : '/admin/tenant/tools',
        { replace: true },
      );
      return;
    }
    if (tabFromUrl && (AI_TABS as readonly string[]).includes(tabFromUrl) && tabFromUrl !== activeTab) {
      setActiveTabState(tabFromUrl as AITab);
    }
  }, [tabFromUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  const targetUserId = userId || (scope === 'user' ? user?.id : undefined);
  const targetTenantId = tenantId || (scope === 'tenant' ? user?.tenant_id : undefined);

  useEffect(() => {
    const apiScope = scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
    loadConfigSummary(targetTenantId, targetUserId, apiScope);
  }, [targetTenantId, targetUserId, scope]);

  if (isLoading) {
    return <LoadingState variant="section" message="Loading AI Configuration..." />;
  }

  const getPageTitle = () => {
    if (scope === 'global') return 'System AI Strategy';
    if (scope === 'tenant') return 'Tenant AI Overrides';
    return 'My AI Configuration';
  };

  return (
    <div className="space-y-6">
      {!embedded && (
        <PageHeader
          title={getPageTitle()}
          subtitle="Manage AI models, providers and task assignments"
          icon={<Cpu className="w-8 h-8" />}
        />
      )}

      {/* Tabs (library SegmentedTabs — desktop/study parity) */}
      <SegmentedTabs
        ariaLabel="AI configuration sections"
        items={[
          { value: 'providers', label: 'Providers' },
          { value: 'models', label: 'Models' },
          { value: 'tasks', label: 'Task Assignments' },
        ]}
        value={activeTab}
        onValueChange={(next) => setActiveTab(next as AITab)}
      />

      {/* Tab Content */}
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow-sm p-6 mb-20">
        {activeTab === 'providers' && (
          <ProviderManager 
            scope={scope} 
            userId={targetUserId} 
            tenantId={targetTenantId} 
          />
        )}

        {activeTab === 'models' && (
          <ModelsTab />
        )}

        {activeTab === 'tasks' && (
          <TaskAssignment 
            scope={scope} 
            userId={targetUserId} 
            tenantId={targetTenantId} 
          />
        )}
      </div>
    </div>
  );
};