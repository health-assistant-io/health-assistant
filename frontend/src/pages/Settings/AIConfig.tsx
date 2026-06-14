import React, { useState, useEffect } from 'react';
import { ProviderManager } from '../../components/settings/ProviderManager';
import { ModelsPage } from '../../components/settings/ModelsPage';
import { TaskAssignment } from '../../components/settings/TaskAssignment';
import { AgentSettings } from '../../components/settings/AgentSettings';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { useAuthStore } from '../../store/slices/authSlice';
import { LoadingState } from '../../components/ui/LoadingState';
import { PageHeader } from '../../components/ui/PageHeader';
import { Cpu } from 'lucide-react';

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
  const [activeTab, setActiveTab] = useState<'providers' | 'models' | 'tasks' | 'agent'>('providers');

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

  const showAgentSettings = scope === 'global' || scope === 'tenant';

  return (
    <div className="space-y-6">
      {!embedded && (
        <PageHeader
          title={getPageTitle()}
          subtitle="Manage AI models, providers and task assignments"
          icon={<Cpu className="w-8 h-8" />}
          showBackButton={true}
        />
      )}

      {/* Tabs */}
      <div className="flex space-x-2 border-b border-gray-200 dark:border-dark-border">
        <button
          onClick={() => setActiveTab('providers')}
          className={`px-4 py-2 font-medium rounded-lg ${
            activeTab === 'providers'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-200 dark:bg-dark-border text-gray-700 dark:text-dark-text'
          }`}
        >
          Providers
        </button>
        <button
          onClick={() => setActiveTab('models')}
          className={`px-4 py-2 font-medium rounded-lg ${
            activeTab === 'models'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-200 dark:bg-dark-border text-gray-700 dark:text-dark-text'
          }`}
        >
          Models
        </button>
        <button
          onClick={() => setActiveTab('tasks')}
          className={`px-4 py-2 font-medium rounded-lg ${
            activeTab === 'tasks'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-200 dark:bg-dark-border text-gray-700 dark:text-dark-text'
          }`}
        >
          Task Assignments
        </button>
        {showAgentSettings && (
          <button
            onClick={() => setActiveTab('agent')}
            className={`px-4 py-2 font-medium rounded-lg ${
              activeTab === 'agent'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-200 dark:bg-dark-border text-gray-700 dark:text-dark-text'
            }`}
          >
            Agent Settings
          </button>
        )}
      </div>

      {/* Tab Content */}
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 mb-20">
        {activeTab === 'providers' && (
          <ProviderManager 
            scope={scope} 
            userId={targetUserId} 
            tenantId={targetTenantId} 
          />
        )}

        {activeTab === 'models' && (
          <ModelsPage />
        )}

        {activeTab === 'tasks' && (
          <TaskAssignment 
            scope={scope} 
            userId={targetUserId} 
            tenantId={targetTenantId} 
          />
        )}

        {activeTab === 'agent' && (
          <AgentSettings
            scope={scope}
            tenantId={targetTenantId}
            userId={targetUserId}
          />
        )}
      </div>
    </div>
  );
};