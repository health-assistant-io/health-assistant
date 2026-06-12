import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Plus, Edit2, Trash2, Calendar, ShieldAlert, History, ChevronDown, ChevronUp, Clock, List } from 'lucide-react';
import { getPatientAllergies, AllergyIntolerance, deletePatientAllergy } from '../../services/allergyService';
import { useUIStore } from '../../store/slices/uiSlice';
import { AllergyModal } from './AllergyModal';

interface Props {
  patientId: string;
}

export const AllergySummary: React.FC<Props> = ({ patientId }) => {
  const { t } = useTranslation();
  const [allergies, setAllergies] = useState<AllergyIntolerance[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedAllergy, setSelectedAllergy] = useState<AllergyIntolerance | undefined>(undefined);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [resolvedViewMode, setResolvedViewMode] = useState<'compact' | 'timeline'>('compact');
  const showConfirmation = useUIStore(state => state.showConfirmation);

  const fetchAllergies = async () => {
    try {
      setLoading(true);
      const data = await getPatientAllergies(patientId);
      setAllergies(data);
    } catch (err) {
      console.error("Failed to fetch allergies", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAllergies();
  }, [patientId]);

  const handleDelete = (allergy: AllergyIntolerance) => {
    showConfirmation({
      title: t('allergies.remove_title'),
      message: t('allergies.remove_confirm', { name: allergy.code.text }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deletePatientAllergy(allergy.id);
          fetchAllergies();
        } catch (err) {
          console.error("Failed to delete allergy", err);
        }
      }
    });
  };

  const activeAllergies = allergies.filter(a => a.clinical_status?.toLowerCase() === 'active');
  const resolvedAllergies = allergies.filter(a => a.clinical_status?.toLowerCase() !== 'active');

  const getCriticalityStyles = (criticality?: string) => {
    switch(criticality) {
      case 'high': return 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800 animate-pulse-slow';
      case 'low': return 'bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-400 border-yellow-200 dark:border-yellow-800';
      default: return 'bg-gray-50 dark:bg-dark-bg text-gray-700 dark:text-dark-text border-gray-200 dark:border-dark-border';
    }
  };

  if (loading) return (
    <div className="animate-pulse bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border w-full h-full">
      <div className="h-4 w-32 bg-gray-200 rounded mb-4"></div>
      <div className="flex space-x-2">
        <div className="h-8 w-24 bg-gray-100 rounded-full"></div>
        <div className="h-8 w-24 bg-gray-100 rounded-full"></div>
      </div>
    </div>
  );

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden w-full h-full">
      <div className="px-4 sm:px-6 py-4 border-b border-gray-50 dark:border-dark-border flex flex-wrap items-center justify-between gap-4 bg-white dark:bg-dark-surface">
        <div className="flex items-center space-x-3 flex-wrap">
          <div className="flex items-center space-x-2">
            <ShieldAlert className="w-5 h-5 text-red-500 shrink-0" />
            <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text whitespace-nowrap">{t('allergies.title')}</h2>
          </div>
          <span className="px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted rounded-full text-[10px] font-bold uppercase shrink-0">
            {activeAllergies.length} {t('allergies.active')}
          </span>
        </div>
        <button 
          onClick={() => { setSelectedAllergy(undefined); setIsModalOpen(true); }}
          className="flex items-center justify-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold shrink-0"
        >
          <Plus className="w-3 h-3" />
          <span>{t('allergies.add_record')}</span>
        </button>
      </div>

      <div className="p-4 sm:p-6 max-h-[400px] overflow-y-auto custom-scrollbar">
        {activeAllergies.length === 0 && resolvedAllergies.length === 0 ? (
          <div className="text-center py-4">
            <p className="text-gray-400 text-sm italic">{t('allergies.no_allergies')}</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-3">
              {activeAllergies.map(allergy => (
                <div 
                  key={allergy.id}
                  className={`relative flex items-center group rounded-xl border px-3 sm:px-4 py-2 transition-all cursor-pointer hover:shadow-md ${getCriticalityStyles(allergy.criticality)} w-full`}
                  onClick={() => setExpandedId(expandedId === allergy.id ? null : allergy.id)}
                >
                  <div className="flex items-start space-x-3 w-full pr-8">
                    <AlertTriangle className={`w-4 h-4 mt-0.5 shrink-0 ${allergy.criticality === 'high' ? 'text-red-600 dark:text-red-400' : 'text-yellow-600 dark:text-yellow-400'}`} />
                    <div className="flex-1 min-w-0">
                      <p className="font-bold text-sm leading-tight dark:text-dark-text break-words">{allergy.code.text}</p>
                      <p className="text-[10px] opacity-70 font-medium uppercase tracking-tighter dark:text-dark-muted break-words">
                        {allergy.category || t('allergies.uncategorized')}
                      </p>
                    </div>
                    {expandedId === allergy.id ? <ChevronUp className="w-4 h-4 ml-1 opacity-40 shrink-0 dark:text-dark-muted" /> : <ChevronDown className="w-4 h-4 ml-1 shrink-0 opacity-40 dark:text-dark-muted" />}
                  </div>

                  <div className="absolute -top-2 -right-2 hidden group-hover:flex items-center space-x-1 z-20">
                    <button 
                      onClick={(e) => { e.stopPropagation(); setSelectedAllergy(allergy); setIsModalOpen(true); }}
                      className="p-1.5 bg-white dark:bg-dark-surface shadow-lg border border-gray-100 dark:border-dark-border rounded-full text-blue-600 dark:text-blue-400 hover:text-blue-700"
                    >
                      <Edit2 className="w-3 h-3" />
                    </button>
                    <button 
                      onClick={(e) => { e.stopPropagation(); handleDelete(allergy); }}
                      className="p-1.5 bg-white dark:bg-dark-surface shadow-lg border border-gray-100 dark:border-dark-border rounded-full text-red-600 dark:text-red-400 hover:text-red-700"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {expandedId && (
              <div className="animate-in slide-in-from-top-2 duration-200 bg-gray-50 dark:bg-dark-bg/50 rounded-2xl p-5 border border-gray-100 dark:border-dark-border">
                {activeAllergies.concat(resolvedAllergies).find(a => a.id === expandedId) && (
                  (() => {
                    const a = activeAllergies.concat(resolvedAllergies).find(a => a.id === expandedId)!;
                    return (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div className="space-y-4">
                          <div>
                            <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('allergies.clinical_timeline')}</h4>
                            <div className="flex flex-col space-y-2 sm:flex-row sm:space-y-0 sm:items-center sm:space-x-4">
                              <div className="flex items-center text-xs sm:text-sm text-gray-700 dark:text-dark-text">
                                <Calendar className="w-4 h-4 mr-2 text-blue-500" />
                                <span>{t('allergies.started')}: {a.onset_date ? new Date(a.onset_date).toLocaleDateString() : t('allergies.unknown')}</span>
                              </div>
                              <div className="flex items-center text-xs sm:text-sm text-gray-700 dark:text-dark-text">
                                <History className="w-4 h-4 mr-2 text-green-500" />
                                <span>{t('allergies.last_event')}: {a.last_occurrence ? new Date(a.last_occurrence).toLocaleDateString() : '—'}</span>
                              </div>
                            </div>
                          </div>
                          {a.note && (
                            <div>
                              <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('allergies.clinical_notes')}</h4>
                              <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed">{a.note}</p>
                            </div>
                          )}
                        </div>
                        <div>
                          <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-2 sm:text-right">{t('allergies.reaction_episodes')}</h4>
                          <div className="space-y-2">
                            {a.reactions && a.reactions.length > 0 ? a.reactions.map((r, i) => (
                              <div key={i} className="bg-white dark:bg-dark-surface p-3 rounded-xl border border-gray-100 dark:border-dark-border flex justify-between items-center">
                                <div className="flex items-center space-x-3">
                                  <div className={`w-2 h-2 rounded-full ${r.severity === 'severe' ? 'bg-red-500' : (r.severity === 'moderate' ? 'bg-yellow-500' : 'bg-blue-500')}`} />
                                  <span className="text-sm font-bold text-gray-800 dark:text-dark-text">{r.manifestation}</span>
                                </div>
                                <span className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-tighter">{r.severity}</span>
                              </div>
                            )) : (
                              <p className="text-sm text-gray-400 text-right italic">{t('allergies.no_reactions')}</p>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })()
                )}
              </div>
            )}

            {resolvedAllergies.length > 0 && (
              <div className="pt-6 border-t border-gray-50 dark:border-dark-border">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('allergies.resolved_history')}</h3>
                  <div className="flex bg-gray-100 dark:bg-dark-bg p-0.5 rounded-lg">
                    <button 
                      onClick={() => setResolvedViewMode('compact')}
                      className={`p-1.5 rounded-md transition-all ${resolvedViewMode === 'compact' ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' : 'text-gray-400 dark:text-dark-muted'}`}
                      title={t('allergies.compact_view')}
                    >
                      <List className="w-3.5 h-3.5" />
                    </button>
                    <button 
                      onClick={() => setResolvedViewMode('timeline')}
                      className={`p-1.5 rounded-md transition-all ${resolvedViewMode === 'timeline' ? 'bg-white dark:bg-dark-surface shadow-sm text-blue-600' : 'text-gray-400 dark:text-dark-muted'}`}
                      title={t('allergies.timeline_view')}
                    >
                      <Clock className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {resolvedViewMode === 'compact' ? (
                  <div className="flex flex-wrap gap-2 opacity-70 grayscale hover:grayscale-0 hover:opacity-100 transition-all">
                    {resolvedAllergies.map(allergy => (
                      <div 
                        key={allergy.id}
                        onClick={() => setExpandedId(expandedId === allergy.id ? null : allergy.id)}
                        className={`px-3 py-1.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-xs font-bold text-gray-600 dark:text-dark-muted cursor-pointer hover:bg-white dark:hover:bg-dark-surface transition-colors flex items-center space-x-2 ${expandedId === allergy.id ? 'ring-2 ring-blue-500/20 border-blue-500/50' : ''}`}
                      >
                        <span>{allergy.code.text}</span>
                        <span className="text-[10px] font-normal opacity-50">• {new Date(allergy.resolved_date!).getFullYear()}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="space-y-6 pl-4 relative before:absolute before:left-0 before:top-2 before:bottom-2 before:w-0.5 before:bg-gray-100 dark:before:bg-dark-border">
                    {resolvedAllergies
                      .sort((a, b) => new Date(b.resolved_date!).getTime() - new Date(a.resolved_date!).getTime())
                      .map(allergy => (
                        <div key={allergy.id} className="relative pl-6 group">
                          <div className="absolute left-[-1.15rem] top-1.5 w-2.5 h-2.5 rounded-full bg-white dark:bg-dark-surface border-2 border-gray-300 dark:border-dark-border group-hover:border-blue-500 transition-colors z-10" />
                          <div 
                            className={`p-4 bg-gray-50/50 dark:bg-dark-bg/30 rounded-2xl border border-gray-100 dark:border-dark-border cursor-pointer hover:border-blue-200 dark:hover:border-blue-900 transition-all ${expandedId === allergy.id ? 'ring-2 ring-blue-500/10 border-blue-500/30' : ''}`}
                            onClick={() => setExpandedId(expandedId === allergy.id ? null : allergy.id)}
                          >
                            <div className="flex justify-between items-start">
                              <div>
                                <h4 className="font-bold text-gray-900 dark:text-dark-text leading-tight">{allergy.code.text}</h4>
                                <p className="text-[10px] text-gray-400 dark:text-dark-muted font-bold uppercase tracking-tighter mt-0.5">{allergy.category}</p>
                              </div>
                              <div className="text-right">
                                <p className="text-xs font-bold text-gray-500 dark:text-dark-muted">
                                  {allergy.onset_date ? new Date(allergy.onset_date).getFullYear() : '?'} — {new Date(allergy.resolved_date!).getFullYear()}
                                </p>
                                <p className="text-[10px] text-gray-400 dark:text-dark-muted font-medium italic mt-0.5">{t('allergies.duration')}: {
                                  allergy.onset_date ? 
                                  `${Math.ceil((new Date(allergy.resolved_date!).getTime() - new Date(allergy.onset_date).getTime()) / (1000 * 60 * 60 * 24 * 30))} mo` 
                                  : t('allergies.unknown')
                                }</p>
                              </div>
                            </div>
                            
                            {allergy.reactions && allergy.reactions.length > 0 && (
                              <div className="mt-2 flex flex-wrap gap-1">
                                {allergy.reactions.map((r, i) => (
                                  <span key={i} className="px-2 py-0.5 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded text-[9px] text-gray-500 dark:text-dark-muted font-bold uppercase tracking-tighter">
                                    {r.manifestation}
                                  </span>
                                ))}
                              </div>
                            )}

                            {expandedId === allergy.id && allergy.note && (
                              <div className="mt-3 pt-3 border-t border-gray-100 dark:border-dark-border animate-in fade-in slide-in-from-top-1">
                                <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed italic">"{allergy.note}"</p>
                              </div>
                            )}
                          </div>
                        </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <AllergyModal 
        isOpen={isModalOpen} 
        onClose={() => { setIsModalOpen(false); setSelectedAllergy(undefined); }} 
        patientId={patientId}
        allergy={selectedAllergy}
        onSuccess={fetchAllergies}
      />
    </div>
  );
};
