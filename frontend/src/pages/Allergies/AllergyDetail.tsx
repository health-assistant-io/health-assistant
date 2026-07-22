import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  ShieldAlert,
  ChevronLeft,
  Info,
  AlertTriangle,
  Users,
  User,
  Clock,
  Edit2,
  Save,
  X,
  Plus,
  Sparkles,
  Database,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import {
  getCatalogAllergy,
  getAllergyUsage,
  updateCatalogAllergy,
  reprocessAllergy,
  AllergyCatalogEntry,
  AllergyUsage,
} from '../../services/allergyService';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useTabScroll } from '../../hooks/useTabScroll';
import { useUIStore } from '../../store/slices/uiSlice';

/**
 * Allergen catalog detail page. Mirrors `MedicationDetail` — three tabs:
 *   - info       : description, typical reactions, category, AI reprocess
 *   - reactions  : (only shown when a current patient context exists) the
 *                  patient's intolerances pointing at this allergen
 *   - management : cross-patient usage table
 *
 * Editing + reprocess only touch the CATALOG entry — patient-instance
 * intolerances are managed from the AllergyList page or the patient chart.
 */
function AllergyDetail() {
  const { t } = useTranslation();
  const { allergyId } = useParams();
  const navigate = useNavigate();
  const setCurrentAllergyId = useUIStore(state => state.setCurrentAllergyId);

  const [allergy, setAllergy] = useState<AllergyCatalogEntry | null>(null);
  const [usage, setUsage] = useState<AllergyUsage[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'info' | 'reactions' | 'management'>('info');
  const tabsRef = useRef<HTMLDivElement>(null);

  useTabScroll(tabsRef, activeTab);

  const [isEditing, setIsEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [formData, setFormData] = useState<Partial<AllergyCatalogEntry>>({});
  const [newReaction, setNewReaction] = useState('');

  useEffect(() => {
    if (allergyId) setCurrentAllergyId(allergyId);
    return () => setCurrentAllergyId(null);
  }, [allergyId, setCurrentAllergyId]);

  useEffect(() => {
    if (allergyId) loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allergyId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [data, usageData] = await Promise.all([
        getCatalogAllergy(allergyId!),
        getAllergyUsage(allergyId!),
      ]);
      setAllergy(data);
      setUsage(usageData);
      setFormData(data);
    } catch (error) {
      console.error('Failed to load allergy details:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleEdit = () => {
    if (isEditing) setFormData(allergy || {});
    setIsEditing(!isEditing);
  };

  const handleSave = async () => {
    if (!allergyId) return;
    setSubmitting(true);
    try {
      const updated = await updateCatalogAllergy(allergyId, formData);
      setAllergy(updated);
      setIsEditing(false);
    } catch (error) {
      console.error('Failed to update allergy:', error);
      alert(t('common.error'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReprocess = async () => {
    if (!allergyId) return;
    setReprocessing(true);
    try {
      const updated = await reprocessAllergy(allergyId);
      setAllergy(updated);
      setFormData(updated);
    } catch (error) {
      console.error('Failed to reprocess allergy:', error);
      alert(t('common.error'));
    } finally {
      setReprocessing(false);
    }
  };

  const handleAddReaction = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newReaction.trim()) return;
    const current = formData.typical_reactions || [];
    if (!current.includes(newReaction.trim())) {
      setFormData({ ...formData, typical_reactions: [...current, newReaction.trim()] });
    }
    setNewReaction('');
  };

  const handleRemoveReaction = (r: string) => {
    setFormData({
      ...formData,
      typical_reactions: (formData.typical_reactions || []).filter(x => x !== r),
    });
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-rose-600 mb-4"></div>
        <p className="text-gray-500 animate-pulse">Loading allergen details…</p>
      </div>
    );
  }

  if (!allergy) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">
          {t('allergies.no_allergies_found', 'No allergen found')}
        </h2>
        <button
          onClick={() => navigate('/allergies')}
          className="mt-4 text-blue-600 hover:underline flex items-center justify-center mx-auto"
        >
          <ChevronLeft className="w-4 h-4 mr-1" />
          {t('allergies.back_to_list', 'Back to allergies')}
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <PageHeader
        title={allergy.name}
        subtitle={
          <div className="flex items-center space-x-2">
            <p className="text-sm text-gray-500 dark:text-dark-muted font-medium">
              {t('allergies.allergen_id', { defaultValue: 'Allergen ID' })}: {allergy.id}
            </p>
            <span className="px-2 py-0.5 bg-amber-50 dark:bg-amber-900/20 rounded text-[10px] font-black uppercase text-amber-600 dark:text-amber-400 border border-amber-100 dark:border-amber-800/30">
              {allergy.category}
            </span>
            {allergy.is_custom && (
              <span className="px-2 py-0.5 bg-amber-50 dark:bg-amber-900/20 rounded text-[10px] font-black uppercase text-amber-600 dark:text-amber-400 border border-amber-100 dark:border-amber-800/30">
                {t('medications.custom_resource')}
              </span>
            )}
          </div>
        }
        icon={<ShieldAlert className="w-8 h-8" />}
        breadcrumbs={[{ label: t('common.allergies'), path: '/allergies' }]}
        showBackButton
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-3">
            {activeTab === 'info' && (
              <>
                {isEditing ? (
                  <>
                    <button
                      onClick={handleToggleEdit}
                      className="px-4 py-2 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 transition-all font-bold text-sm flex items-center space-x-2"
                    >
                      <X className="w-4 h-4" /> <span>{t('common.cancel')}</span>
                    </button>
                    <button
                      onClick={handleSave}
                      disabled={submitting}
                      className="px-6 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-all shadow-lg shadow-emerald-200/50 font-bold text-sm active:scale-95 flex items-center space-x-2"
                    >
                      <Save className="w-4 h-4" /> <span>{t('common.save')}</span>
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={handleReprocess}
                      disabled={reprocessing}
                      className="px-4 py-2 bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-100 dark:border-indigo-900/30 text-indigo-600 dark:text-indigo-400 rounded-xl hover:bg-indigo-100 transition-all font-semibold shadow-sm active:scale-95 text-sm flex items-center space-x-2"
                    >
                      <Sparkles className="w-4 h-4" />{' '}
                      <span>{reprocessing ? t('allergies.reprocessing', 'Reprocessing…') : t('allergies.ai_reprocess', 'AI reprocess')}</span>
                    </button>
                    <button
                      onClick={handleToggleEdit}
                      className="px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-brand-navy dark:text-dark-text rounded-xl hover:bg-gray-50 transition-all font-semibold shadow-sm text-sm flex items-center space-x-2"
                    >
                      <Edit2 className="w-4 h-4" /> <span>{t('common.edit')}</span>
                    </button>
                  </>
                )}
              </>
            )}
            <a
              href={`/catalogs?type=allergy&item=${allergyId}`}
              className="p-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-gray-400 hover:text-purple-600 transition-all shadow-sm"
              title={t('allergies.manage_in_catalog', 'Manage in Catalogs')}
            >
              <Database className="w-5 h-5" />
            </a>
          </div>
        }
      />

      {/* Tabs */}
      <div
        ref={tabsRef}
        className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl w-fit mb-8 border border-gray-200 dark:border-dark-border scroll-mt-32"
      >
        <button
          onClick={() => setActiveTab('info')}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'info' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
        >
          <Info className="w-4 h-4" />
          <span>{t('medications.general_info', 'Info')}</span>
        </button>
        <button
          onClick={() => setActiveTab('reactions')}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'reactions' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
        >
          <AlertTriangle className="w-4 h-4" />
          <span>{t('allergies.reactions_tab', 'Reactions')}</span>
        </button>
        <button
          onClick={() => setActiveTab('management')}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'management' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
        >
          <Users className="w-4 h-4" />
          <span>{t('allergies.management', 'Management')}</span>
        </button>
      </div>

      <div className="grid grid-cols-1 gap-8">
        {/* INFO TAB */}
        {activeTab === 'info' && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 animate-in fade-in duration-500">
            <div className="xl:col-span-2 space-y-6">
              <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-8 shadow-sm">
                <h3 className="text-lg font-black text-brand-navy dark:text-dark-text mb-6 flex items-center uppercase tracking-tight">
                  <Info className="w-5 h-5 mr-3 text-blue-500" />
                  {t('medications.description', 'Description')}
                </h3>
                <div className="prose dark:prose-invert max-w-none">
                  {isEditing ? (
                    <textarea
                      className="w-full bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl p-4 text-sm resize-none h-40"
                      value={formData.description || ''}
                      onChange={e => setFormData({ ...formData, description: e.target.value })}
                      placeholder={t('allergies.description_placeholder', 'Describe the allergen…')}
                    />
                  ) : (
                    <div className="text-gray-700 dark:text-dark-text leading-relaxed text-lg font-medium">
                      {!allergy.description ? (
                        <p className="italic text-gray-400">
                          {t('medications.no_description', 'No description available.')}
                        </p>
                      ) : allergy.description.includes('</') || allergy.description.includes('<br') ? (
                        <div dangerouslySetInnerHTML={{ __html: allergy.description }} />
                      ) : (
                        <ReactMarkdown>{allergy.description}</ReactMarkdown>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="space-y-8">
              <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
                <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em] mb-6 flex items-center">
                  <AlertTriangle className="w-4 h-4 mr-2 text-rose-500" />
                  {t('allergies.typical_reactions', 'Typical reactions')}
                </h4>
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    {(isEditing ? formData.typical_reactions : allergy.typical_reactions)?.map((r, idx) => (
                      <span
                        key={idx}
                        className="px-3 py-1.5 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400 rounded-xl text-xs font-bold border border-rose-100 dark:border-rose-900/30 flex items-center group"
                      >
                        {r}
                        {isEditing && (
                          <button
                            onClick={() => handleRemoveReaction(r)}
                            className="ml-2 p-0.5 hover:bg-rose-200 rounded-full transition-colors"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        )}
                      </span>
                    ))}
                  </div>
                  {isEditing && (
                    <form onSubmit={handleAddReaction} className="flex items-center space-x-2 mt-4">
                      <input
                        type="text"
                        className="flex-1 px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-xs outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder={t('allergies.add_reaction_placeholder', 'Add a typical reaction')}
                        value={newReaction}
                        onChange={e => setNewReaction(e.target.value)}
                      />
                      <button
                        type="submit"
                        className="p-2 bg-blue-100 text-blue-600 rounded-xl hover:bg-blue-200 transition-colors"
                      >
                        <Plus className="w-4 h-4" />
                      </button>
                    </form>
                  )}
                </div>
              </div>

              <div className="bg-blue-50/50 dark:bg-blue-900/10 rounded-[2rem] p-8 border border-blue-100/50 dark:border-blue-900/20">
                <h4 className="text-[10px] font-black text-blue-600 uppercase tracking-[0.2em] mb-4 flex items-center">
                  <Clock className="w-4 h-4 mr-2" />
                  {t('allergies.category_info', 'Category')}
                </h4>
                <p className="text-blue-900 dark:text-blue-300 font-black uppercase tracking-widest">
                  {allergy.category}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* REACTIONS TAB (current patient's intolerance for this allergen) */}
        {activeTab === 'reactions' && (
          <div className="space-y-8 animate-in slide-in-from-bottom-4 duration-500">
            {usage.length === 0 ? (
              <div className="py-20 text-center bg-gray-50 dark:bg-dark-bg/30 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
                <AlertTriangle className="w-16 h-16 text-gray-200 mx-auto mb-6" />
                <h4 className="text-lg font-bold text-gray-500">
                  {t('allergies.no_recorded_reactions', 'No recorded reactions')}
                </h4>
                <p className="text-gray-400 text-sm mt-2">
                  {t('allergies.no_recorded_reactions_desc', 'No patient has this allergen on their chart yet.')}
                </p>
              </div>
            ) : (
              <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-8">
                <h3 className="text-xs font-black text-gray-400 uppercase tracking-[0.3em] mb-8">
                  {t('allergies.reaction_episodes')}
                </h3>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-50 dark:divide-dark-border">
                    <thead>
                      <tr>
                        <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
                          {t('patients.title', 'Patient')}
                        </th>
                        <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
                          {t('allergies.modal.criticality')}
                        </th>
                        <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
                          {t('allergies.modal.onset_date')}
                        </th>
                        <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">
                          {t('allergies.modal.reaction_episodes')}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                      {usage.map(item => (
                        <tr
                          key={item.allergy.id}
                          className="hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors cursor-pointer"
                          onClick={() => navigate(`/patients/${item.patient.id}`)}
                        >
                          <td className="px-6 py-5 text-sm font-bold text-gray-900 dark:text-dark-text">
                            {item.patient.name?.given?.join(' ') ?? ''} {item.patient.name?.family ?? ''}
                          </td>
                          <td className="px-6 py-5">
                            <span
                              className={`px-2 py-1 rounded-lg text-[9px] font-black uppercase border ${(item.allergy.criticality ?? '').toUpperCase() === 'HIGH' ? 'bg-rose-50 text-rose-700 border-rose-100' : 'bg-gray-50 text-gray-500 border-gray-100'}`}
                            >
                              {item.allergy.criticality || '—'}
                            </span>
                          </td>
                          <td className="px-6 py-5 text-xs text-gray-500 dark:text-dark-muted font-medium">
                            {item.allergy.onset_date
                              ? new Date(item.allergy.onset_date).toLocaleDateString()
                              : '—'}
                          </td>
                          <td className="px-6 py-5">
                            {item.allergy.reactions && item.allergy.reactions.length > 0 ? (
                              <div className="flex flex-wrap gap-1">
                                {item.allergy.reactions.map((r, i) => (
                                  <span
                                    key={i}
                                    className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase border ${r.severity === 'SEVERE' ? 'bg-rose-50 text-rose-700 border-rose-100' : r.severity === 'MODERATE' ? 'bg-amber-50 text-amber-700 border-amber-100' : 'bg-blue-50 text-blue-700 border-blue-100'}`}
                                  >
                                    {r.manifestation}
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <span className="text-xs text-gray-400 italic">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* MANAGEMENT TAB (cross-patient usage) */}
        {activeTab === 'management' && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 animate-in fade-in duration-500">
            <div className="xl:col-span-2 space-y-6">
              <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border overflow-hidden shadow-sm">
                <div className="p-8 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
                  <h3 className="text-lg font-black text-brand-navy dark:text-dark-text uppercase tracking-tight flex items-center">
                    <Users className="w-5 h-5 mr-3 text-purple-500" />
                    {t('allergies.patient_usage', { defaultValue: 'Patient usage' })}
                  </h3>
                  <div className="px-4 py-1.5 bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 rounded-xl text-xs font-black uppercase tracking-widest border border-purple-100 dark:border-purple-800/30">
                    {usage.length} {t('allergies.affected_patients', { defaultValue: 'patients' })}
                  </div>
                </div>

                <div className="divide-y divide-gray-50 dark:divide-dark-border">
                  {usage.length > 0 ? (
                    usage.map(item => (
                      <div
                        key={item.allergy.id}
                        className="flex items-center justify-between p-8 hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 cursor-pointer transition-all group"
                        onClick={() => navigate(`/patients/${item.patient.id}`)}
                      >
                        <div className="flex items-center space-x-6">
                          <div className="w-14 h-14 bg-purple-50 dark:bg-purple-900/30 rounded-2xl flex items-center justify-center text-purple-600 border border-purple-100 dark:border-purple-800/30 shadow-sm">
                            <User className="w-6 h-6" />
                          </div>
                          <div>
                            <p className="text-lg font-black text-gray-900 dark:text-dark-text group-hover:text-blue-600 transition-colors">
                              {item.patient.name?.given?.join(' ') ?? ''} {item.patient.name?.family ?? ''}
                            </p>
                            <p className="text-xs text-gray-400 font-mono uppercase font-black tracking-widest mt-1">
                              MRN: {item.patient.mrn || 'N/A'}
                            </p>
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="flex items-center justify-end space-x-2 mb-1">
                            <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">
                              {t('allergies.criticality_label', 'Criticality')}
                            </span>
                            <span className="text-sm font-bold text-gray-900 dark:text-dark-text">
                              {item.allergy.criticality || 'LOW'}
                            </span>
                          </div>
                          <p className="text-xs text-rose-600 dark:text-rose-400 font-black uppercase tracking-widest">
                            {item.allergy.reactions?.length || 0}{' '}
                            {t('allergies.reactions_count', { defaultValue: 'reactions' })}
                          </p>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="py-20 text-center opacity-30">
                      <Users className="w-16 h-16 mx-auto mb-4" />
                      <p className="text-sm font-black uppercase tracking-[0.2em]">
                        {t('allergies.no_patients_affected', 'No patients affected')}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default AllergyDetail;
