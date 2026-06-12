import { useMemo } from 'react';
import { useAIConfigStore } from '../store/slices/aiConfigSlice';
import { AIProvider, AIModel } from '../api/aiConfig';

interface ActiveAITask {
  provider: AIProvider | null;
  model: AIModel | null;
}

export function useActiveAITask(taskType?: string): ActiveAITask {
  const configSummary = useAIConfigStore(state => state.configSummary);

  return useMemo(() => {
    if (!configSummary) {
      return { provider: null, model: null };
    }

    if (!taskType) {
       return { 
           provider: configSummary.default?.provider || null, 
           model: configSummary.default?.model || null 
       };
    }

    // Try to get specific task config
    const taskConfig = (configSummary as any)[taskType];
    
    if (taskConfig && (taskConfig.provider || taskConfig.model)) {
      return {
        provider: taskConfig.provider || null,
        model: taskConfig.model || null
      };
    }

    // Fallback to default
    return {
      provider: configSummary.default?.provider || null,
      model: configSummary.default?.model || null
    };
  }, [configSummary, taskType]);
}
