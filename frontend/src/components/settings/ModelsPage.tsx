import React, { useState, useEffect } from 'react';
import { useAIConfigStore } from '../../store/slices/aiConfigSlice';
import { Cpu, ChevronDown, ChevronRight } from 'lucide-react';
import { ModelManager } from './ModelManager';

export const ModelsPage: React.FC = () => {
  const {
    providers,
    loadProviders,
    error,
    clearError,
  } = useAIConfigStore();

  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
          Models per Provider
        </h3>
        <span className="px-3 py-1 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted text-[10px] font-black uppercase tracking-widest rounded-full">
          {providers.length} Providers
        </span>
      </div>

      {error && (
        <div className="p-3 bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-lg">
          {error}
          <button onClick={clearError} className="ml-2 text-sm underline">Clear</button>
        </div>
      )}

      {providers.length === 0 && (
        <div className="p-6 text-center text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-dark-bg rounded-lg">
          <p>No providers configured yet.</p>
          <p className="mt-2">Go to the Providers tab to add a provider first.</p>
        </div>
      )}

      <div className="space-y-4">
        {providers.map((provider) => {
          const isExpanded = expandedProviderId === provider.id;

          return (
            <div
              key={provider.id}
              className={`bg-white dark:bg-dark-surface rounded-xl border transition-all ${isExpanded ? 'border-blue-200 shadow-sm' : 'border-gray-100 dark:border-dark-border'}`}
            >
              {/* Provider Header */}
              <div 
                className="p-4 flex items-center justify-between cursor-pointer group"
                onClick={() => setExpandedProviderId(isExpanded ? null : provider.id)}
              >
                <div className="flex items-center gap-4">
                  <div className={`p-2 rounded-lg transition-colors ${isExpanded ? 'bg-blue-600 text-white' : 'bg-gray-100 dark:bg-dark-bg text-gray-500 group-hover:bg-blue-50'}`}>
                    {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                  </div>
                  <div>
                    <h4 className="text-md font-bold text-gray-900 dark:text-dark-text flex items-center">
                      {provider.name}
                    </h4>
                    <p className="text-xs text-gray-400 font-medium">
                      {provider.provider_type}
                    </p>
                  </div>
                </div>
              </div>

              {/* Expanded Content */}
              {isExpanded && (
                <div className="px-4 pb-4 animate-in slide-in-from-top-2 duration-200">
                  <div className="border-t border-gray-100 dark:border-dark-border pt-4">
                    <ModelManager provider={provider} />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
