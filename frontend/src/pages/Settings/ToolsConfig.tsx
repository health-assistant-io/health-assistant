import React from 'react';
import { Wrench } from 'lucide-react';
import { AgentSettings } from '../../components/settings/AgentSettings';
import { PageHeader } from '../../components/ui/PageHeader';

/**
 * Tools configuration (ADR-0033, domain tools.agent): run-behavior knobs
 * that are not model assignment. The AI config tab hosts exactly ai.*
 * domains — agent behavior moved here in plan 27 Phase 4.
 *
 * Exported for the settings-map conformance test.
 */
export const TOOLS_TAB_SECTIONS = ['agent'] as const;

interface ToolsConfigProps {
  scope?: 'global' | 'tenant' | 'user';
  tenantId?: string;
  userId?: string;
  embedded?: boolean;
}

export const ToolsConfig: React.FC<ToolsConfigProps> = ({
  scope = 'user',
  tenantId,
  userId,
  embedded = false,
}) => {
  const getPageTitle = () => {
    if (scope === 'global') return 'System Tools';
    if (scope === 'tenant') return 'Tenant Tools';
    return 'My Tools';
  };

  return (
    <div className="space-y-6">
      {!embedded && (
        <PageHeader
          title={getPageTitle()}
          subtitle="Agent behavior and tool capabilities"
          icon={<Wrench className="w-8 h-8" />}
        />
      )}

      <div className="bg-white dark:bg-dark-surface rounded-lg shadow-sm p-6 mb-20">
        <AgentSettings scope={scope} tenantId={tenantId} userId={userId} />
      </div>
    </div>
  );
};
