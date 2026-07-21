import React, { useEffect, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, Wrench, Search, Activity, Pill, Stethoscope, FileText, Bot, Box, CheckCircle2 } from 'lucide-react';
import { AIToolInfo, getAvailableTools } from '../../services/aiAssistanceService';
import { AIBadge } from '../ui/AIBadge';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId?: string;
}

interface ToolCategory {
  id: string;
  name: string;
  icon: React.ElementType;
  tools: AIToolInfo[];
}

export const AIToolsModal: React.FC<Props> = ({ isOpen, onClose, patientId, examinationId }) => {
  const { t } = useTranslation();
  const [tools, setTools] = useState<AIToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [activeCategoryId, setActiveCategoryId] = useState<string>('all');
  const [selectedTool, setSelectedTool] = useState<AIToolInfo | null>(null);

  useEffect(() => {
    if (isOpen) {
      if (!patientId) {
        setTools([]);
        setLoading(false);
        return;
      }
      setLoading(true);
      getAvailableTools(patientId, examinationId)
        .then(setTools)
        .catch(err => console.error("Failed to fetch tools", err))
        .finally(() => setLoading(false));
    }
  }, [isOpen, patientId, examinationId]);

  const categories = useMemo(() => {
    const cats: Record<string, ToolCategory> = {
      biomarkers: { id: 'biomarkers', name: 'Biomarkers & Telemetry', icon: Activity, tools: [] },
      medications: { id: 'medications', name: 'Medications', icon: Pill, tools: [] },
      clinical: { id: 'clinical', name: 'Examinations & Events', icon: Stethoscope, tools: [] },
      patient: { id: 'patient', name: 'Patient Context', icon: FileText, tools: [] },
      integrations: { id: 'integrations', name: 'Integrations', icon: Box, tools: [] },
      other: { id: 'other', name: 'Other Tools', icon: Wrench, tools: [] },
    };

    tools.forEach(tool => {
      if (tool.source === 'integration') {
        cats.integrations.tools.push(tool);
        return;
      }
      
      const n = tool.name;
      if (n.includes('biomarker')) {
        cats.biomarkers.tools.push(tool);
      } else if (n.includes('medication')) {
        cats.medications.tools.push(tool);
      } else if (n.includes('examination') || n.includes('clinical_event')) {
        cats.clinical.tools.push(tool);
      } else if (n.includes('patient') || n.includes('document') || n.includes('system_time')) {
        cats.patient.tools.push(tool);
      } else {
        cats.other.tools.push(tool);
      }
    });

    return Object.values(cats).filter(c => c.tools.length > 0);
  }, [tools]);

  const filteredCategories = useMemo(() => {
    const q = searchTerm.toLowerCase();
    return categories.map(cat => ({
      ...cat,
      tools: cat.tools.filter(t => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q))
    })).filter(cat => cat.tools.length > 0);
  }, [categories, searchTerm]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedTool) {
          setSelectedTool(null);
        } else {
          onClose();
        }
      }
    };
    if (isOpen) window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose, selectedTool]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-modal flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200" onClick={onClose}>
      <div 
        className="bg-white dark:bg-dark-surface w-full max-w-4xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[85vh] h-[85vh]"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-50 dark:border-dark-border flex items-center justify-between shrink-0 bg-gradient-to-r from-blue-50/50 to-white dark:from-blue-900/10 dark:to-dark-surface">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">{t('ai_labels.agent_capabilities', 'AI Agent Capabilities')}</h2>
                <AIBadge taskType="chat" />
              </div>
              <p className="text-xs text-gray-500 dark:text-dark-muted font-medium mt-0.5">Explore the tools and actions available to the LLM</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Sidebar */}
          <div className="w-64 border-r border-gray-50 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20 flex flex-col shrink-0">
            <div className="p-4 border-b border-gray-50 dark:border-dark-border">
              <div className="relative">
                <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search tools..."
                  className="w-full pl-9 pr-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text transition-all"
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-1 custom-scrollbar">
              <button
                onClick={() => setActiveCategoryId('all')}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-bold transition-all ${
                  activeCategoryId === 'all' 
                    ? 'bg-blue-600 text-white shadow-md' 
                    : 'text-gray-600 dark:text-dark-text hover:bg-white dark:hover:bg-dark-surface hover:shadow-sm'
                }`}
              >
                <Wrench className={`w-4 h-4 ${activeCategoryId === 'all' ? 'text-blue-200' : 'text-gray-400'}`} />
                <span>All Capabilities</span>
                <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded-full ${activeCategoryId === 'all' ? 'bg-blue-700' : 'bg-gray-200 dark:bg-dark-border'}`}>
                  {tools.length}
                </span>
              </button>
              
              <div className="pt-2 pb-1 px-3 text-[10px] font-black uppercase tracking-widest text-gray-400 dark:text-dark-muted">Categories</div>
              
              {filteredCategories.map(cat => {
                const Icon = cat.icon;
                const isActive = activeCategoryId === cat.id;
                return (
                  <button
                    key={cat.id}
                    onClick={() => setActiveCategoryId(cat.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-bold transition-all ${
                      isActive 
                        ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' 
                        : 'text-gray-600 dark:text-dark-text hover:bg-white dark:hover:bg-dark-surface hover:shadow-sm'
                    }`}
                  >
                    <Icon className={`w-4 h-4 ${isActive ? 'text-blue-500' : 'text-gray-400'}`} />
                    <span>{cat.name}</span>
                    <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded-full ${isActive ? 'bg-blue-200 text-blue-800 dark:bg-blue-800 dark:text-blue-200' : 'bg-gray-200 dark:bg-dark-border'}`}>
                      {cat.tools.length}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Main Content */}
          <div className="flex-1 overflow-y-auto p-6 bg-white dark:bg-dark-surface custom-scrollbar">
            {!patientId ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-400 space-y-3">
                <Bot className="w-12 h-12 opacity-20" />
                <p className="text-sm font-bold text-center">Select a patient to view available agent capabilities.</p>
              </div>
            ) : loading ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-400 space-y-4">
                <div className="w-8 h-8 border-4 border-blue-200 border-t-blue-500 rounded-full animate-spin"></div>
                <p className="text-sm font-bold">Discovering AI capabilities...</p>
              </div>
            ) : filteredCategories.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-gray-400 space-y-3">
                <Search className="w-12 h-12 opacity-20" />
                <p className="text-sm font-bold">No tools found matching "{searchTerm}"</p>
              </div>
            ) : selectedTool ? (
              <div className="animate-in fade-in slide-in-from-right-4 duration-200 h-full flex flex-col">
                <div className="flex items-center gap-3 mb-6 pb-4 border-b border-gray-100 dark:border-dark-border shrink-0">
                  <button 
                    onClick={() => setSelectedTool(null)}
                    className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors text-gray-400"
                  >
                    <X className="w-5 h-5" />
                  </button>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text font-mono truncate">{selectedTool.name}</h3>
                      {selectedTool.source === 'integration' && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300">
                          Integration
                        </span>
                      )}
                      {selectedTool.name.startsWith('propose_') && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                          HITL Action
                        </span>
                      )}
                    </div>
                    <p className="text-xs font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest">Tool Documentation</p>
                  </div>
                </div>
                
                <div className="flex-1 overflow-y-auto space-y-6 pr-2 custom-scrollbar">
                  {/* Description Card */}
                  <div className="bg-gray-50/50 dark:bg-dark-bg/50 rounded-2xl border border-gray-100 dark:border-dark-border p-5 shadow-sm">
                    <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-3">Description</h4>
                    <p className="text-sm text-gray-700 dark:text-dark-text leading-relaxed whitespace-pre-wrap">
                      {selectedTool.description}
                    </p>
                  </div>

                  {/* Arguments Card */}
                  <div className="bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border p-5 shadow-sm">
                    <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-4">Input Arguments</h4>
                    
                    {selectedTool.schema?.properties && Object.keys(selectedTool.schema.properties).length > 0 ? (
                      <div className="space-y-3">
                        {Object.entries(selectedTool.schema.properties).map(([argName, argDetails]: [string, any]) => {
                          const isRequired = selectedTool.schema?.required?.includes(argName);
                          // Determine type (handle anyOf nulls from Pydantic V2)
                          let typeStr = argDetails.type;
                          if (!typeStr && argDetails.anyOf) {
                            const types = argDetails.anyOf.map((t: any) => t.type).filter(Boolean);
                            typeStr = types.join(' | ');
                          }
                          
                          return (
                            <div key={argName} className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-4 p-3 rounded-xl border border-gray-50 dark:border-white/5 bg-gray-50/30 dark:bg-dark-bg/30">
                              <div className="sm:w-1/3 shrink-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-bold font-mono text-indigo-600 dark:text-indigo-400 break-all">{argName}</span>
                                  {isRequired && (
                                    <span className="text-[8px] font-black uppercase tracking-wider text-rose-500 bg-rose-50 dark:bg-rose-500/10 dark:text-rose-400 px-1.5 py-0.5 rounded">Required</span>
                                  )}
                                </div>
                                <div className="text-[10px] font-mono text-gray-400 dark:text-dark-muted mt-1">
                                  {typeStr || 'any'}
                                </div>
                              </div>
                              <div className="sm:w-2/3 text-xs text-gray-600 dark:text-dark-text leading-relaxed">
                                {argDetails.description || argDetails.title || 'No description provided.'}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-400 italic">This tool does not require any input arguments.</p>
                    )}
                  </div>
                  
                  {/* Expected Output Card */}
                  <div className="bg-gray-50/50 dark:bg-dark-bg/50 rounded-2xl border border-gray-100 dark:border-dark-border p-5 shadow-sm">
                    <h4 className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2">Expected Output</h4>
                    <p className="text-xs text-gray-600 dark:text-dark-text leading-relaxed">
                      {selectedTool.name.startsWith('propose_') 
                        ? 'Returns a HITL (Human-in-the-Loop) task payload which will render an interactive confirmation card in the chat interface.'
                        : 'Returns a JSON string containing the requested clinical data or a confirmation message.'}
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-8">
                {filteredCategories
                  .filter(cat => activeCategoryId === 'all' || activeCategoryId === cat.id)
                  .map(cat => {
                    const Icon = cat.icon;
                    return (
                      <div key={cat.id} className="animate-in fade-in slide-in-from-bottom-2">
                        <div className="flex items-center gap-2 mb-4 border-b border-gray-100 dark:border-dark-border pb-2">
                          <Icon className="w-5 h-5 text-blue-500" />
                          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{cat.name}</h3>
                        </div>
                        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                          {cat.tools.map((tool, i) => (
                            <button 
                              key={i} 
                              onClick={() => setSelectedTool(tool)}
                              className="w-full text-left bg-gray-50/50 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border rounded-2xl p-4 hover:shadow-md hover:border-blue-200 dark:hover:border-blue-900/50 transition-all focus:outline-none focus:ring-2 focus:ring-blue-500/20"
                            >
                              <div className="flex items-start gap-3">
                                <div className="mt-0.5">
                                  <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                                </div>
                                <div className="min-w-0">
                                  <div className="flex items-center gap-2 mb-1">
                                    <h4 className="text-sm font-bold text-gray-900 dark:text-dark-text truncate font-mono text-[13px]">{tool.name}</h4>
                                    {tool.source === 'integration' && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300">
                                        Integration
                                      </span>
                                    )}
                                    {tool.name.startsWith('propose_') && (
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                                        HITL Action
                                      </span>
                                    )}
                                  </div>
                                  <p className="text-xs text-gray-600 dark:text-dark-muted leading-relaxed line-clamp-3" title={tool.description}>
                                    {tool.description}
                                  </p>
                                </div>
                              </div>
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};
