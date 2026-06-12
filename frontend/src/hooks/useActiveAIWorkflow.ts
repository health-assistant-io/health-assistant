import { useMemo } from 'react';
import { useAIConfigStore } from '../store/slices/aiConfigSlice';
import { AIProvider, AIModel } from '../api/aiConfig';

export interface ActiveAITask {
  taskType: string;
  provider: AIProvider | null;
  model: AIModel | null;
}

// Map UI workflows to the backend task_types they utilize
const WORKFLOW_MAPPING: Record<string, string[]> = {
  'full_reconstruction': ['ocr', 'nlp'],
  'fast_extraction': ['nlp'],
  'smart_extraction_upload': ['ocr'],
  'magic_fill': ['magic_fill_examination'],
  'clinical_chat': ['chat'],
  'medication_audit': ['medication_interaction'],
  'biomarker_definition': ['define_biomarker'],
  'medication_definition': ['define_medication'],
};

export function useActiveAIWorkflow(workflowOrTaskType?: string): ActiveAITask[] {
  const configSummary = useAIConfigStore(state => state.configSummary);

  return useMemo(() => {
    if (!configSummary) {
      return [];
    }

    if (!workflowOrTaskType) {
       return [{ 
           taskType: 'default',
           provider: configSummary.default?.provider || null, 
           model: configSummary.default?.model || null 
       }];
    }

    // Determine if the input is a known workflow that maps to multiple tasks
    const taskTypes = WORKFLOW_MAPPING[workflowOrTaskType] || [workflowOrTaskType];

    return taskTypes.map(taskType => {
      const taskConfig = (configSummary as any)[taskType];
      
      if (taskConfig && (taskConfig.provider || taskConfig.model)) {
        return {
          taskType: taskType,
          provider: taskConfig.provider || null,
          model: taskConfig.model || null
        };
      }

      // Fallback to default
      return {
        taskType: taskType,
        provider: configSummary.default?.provider || null,
        model: configSummary.default?.model || null
      };
    });
  }, [configSummary, workflowOrTaskType]);
}
