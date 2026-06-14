import React, { useState, useEffect } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { Save, Info } from 'lucide-react';
import { toast } from 'react-toastify';

interface AgentSettingsProps {
  scope: 'global' | 'tenant' | 'user';
  tenantId?: string;
  userId?: string;
}

export const AgentSettings: React.FC<AgentSettingsProps> = ({ scope, tenantId, userId }) => {
  const { configSummary, updateAISettings, isLoading } = useAIConfigStore();
  const [maxIterations, setMaxIterations] = useState(20);

  useEffect(() => {
    if (configSummary) {
      setMaxIterations(configSummary.ai_agent_max_iterations || 20);
    }
  }, [configSummary]);

  const handleSave = async () => {
    try {
      const apiScope = scope === 'global' ? 'SYSTEM' : scope === 'tenant' ? 'TENANT' : 'USER';
      await updateAISettings({ ai_agent_max_iterations: maxIterations }, tenantId, userId, apiScope);
      toast.success('AI Agent settings updated successfully');
    } catch (error) {
      console.error('Failed to update settings:', error);
      toast.error('Failed to update AI Agent settings');
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="bg-blue-50 dark:bg-blue-900/20 p-4 rounded-lg flex items-start space-x-3">
        <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 mt-0.5" />
        <div className="text-sm text-blue-700 dark:text-blue-300">
          <p className="font-semibold mb-1">Reasoning Loop Configuration</p>
          <p>
            The maximum iterations setting controls how many sequential steps the AI can take when reasoning about a complex request. 
            Higher values allow for deeper analysis but may increase latency and API costs.
          </p>
        </div>
      </div>

      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">
            Max Reasoning Iterations
          </label>
          <div className="flex items-center space-x-4">
            <input
              type="range"
              min="1"
              max="50"
              value={maxIterations}
              onChange={(e) => setMaxIterations(parseInt(e.target.value))}
              className="flex-grow h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
            />
            <input
              type="number"
              min="1"
              max="50"
              value={maxIterations}
              onChange={(e) => setMaxIterations(parseInt(e.target.value))}
              className="w-16 px-2 py-1 text-center border border-gray-300 dark:border-dark-border rounded-md bg-white dark:bg-dark-surface dark:text-white"
            />
          </div>
          <p className="mt-1 text-xs text-gray-500">
            Recommended: 15-25 for complex medical reasoning.
          </p>
        </div>

        <div className="pt-4 border-t border-gray-200 dark:border-dark-border">
          <button
            onClick={handleSave}
            disabled={isLoading}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50 transition-colors"
          >
            <Save className="w-4 h-4" />
            <span>{isLoading ? 'Saving...' : 'Save Settings'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
