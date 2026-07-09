import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  Pill, 
  ChevronLeft, 
  ArrowLeft,
  Info, 
  AlertTriangle, 
  Stethoscope, 
  Users, 
  Calendar,
  User,
  Clock,
  ClipboardList,
  Edit2,
  Save,
  X,
  Plus,
  Trash2,
  Sparkles,
  Activity,
  ArrowRight,
  ExternalLink,
  ChevronRight,
  Bell,
  Database
} from 'lucide-react';
import { 
  getCatalogMedication, 
  getMedicationUsage, 
  updateCatalogMedication,
  reprocessMedication,
  getPatientMedications,
  MedicationCatalogEntry, 
  MedicationUsage,
  MedicationRecord
} from '../../services/medicationService';
import { getExamination } from '../../services/examinationService';
import { RichTextEditor } from '../../components/ui/RichTextEditor';
import ReactMarkdown from 'react-markdown';

import { MedicationReminders } from '../../components/medications/MedicationReminders';
import { UniversalCalendar } from '../../components/ui/UniversalCalendar';
import { adaptMedicationToEvents } from '../../utils/calendarUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useTabScroll } from '../../hooks/useTabScroll';
import { startOfMonth, endOfMonth, addMonths } from 'date-fns';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';

function MedicationDetail() {
  const { t } = useTranslation();
  const { medicationId } = useParams();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const setCurrentMedicationId = useUIStore(state => state.setCurrentMedicationId);
  
  const [medication, setMedication] = useState<MedicationCatalogEntry | null>(null);
  const [usage, setUsage] = useState<MedicationUsage[]>([]);
  const [patientRecords, setPatientRecords] = useState<MedicationRecord[]>([]);
  const [linkedExams, setLinkedExams] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'info' | 'prescription' | 'management'>('info');
  const tabsRef = useRef<HTMLDivElement>(null);

  // Auto-scroll when tab changes
  useTabScroll(tabsRef, activeTab);

  // Static calendar events for this specific medication
  const staticCalendarEvents = useMemo(() => {
    if (!patientRecords.length) return [];
    
    // Calculate for a reasonable range around today
    const start = startOfMonth(new Date());
    const end = endOfMonth(addMonths(start, 2));
    
    let allEvents: any[] = [];
    patientRecords.forEach(record => {
        allEvents = [...allEvents, ...adaptMedicationToEvents(record, start, end)];
    });
    return allEvents;
  }, [patientRecords]);
  
  // Edit state
  const [isEditing, setIsEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [formData, setFormData] = useState<Partial<MedicationCatalogEntry>>({});
  const [newSideEffect, setNewSideEffect] = useState('');

  useEffect(() => {
    if (medicationId) {
      setCurrentMedicationId(medicationId);
    }
    return () => setCurrentMedicationId(null);
  }, [medicationId, setCurrentMedicationId]);

  useEffect(() => {
    if (medicationId) {
      loadData();
    }
  }, [medicationId, currentPatient?.id]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [medData, usageData] = await Promise.all([
        getCatalogMedication(medicationId!),
        getMedicationUsage(medicationId!)
      ]);
      setMedication(medData);
      setUsage(usageData);
      setFormData(medData);

      if (currentPatient?.id) {
        const allPatientMeds = await getPatientMedications(currentPatient.id);
        const relevantMeds = allPatientMeds.filter(m => m.code.catalog_id === medicationId);
        setPatientRecords(relevantMeds);
        
        // Fetch linked examinations
        const examIds = [...new Set(relevantMeds.map(m => m.examination_id).filter(Boolean))] as string[];
        if (examIds.length > 0) {
          const exams = await Promise.all(examIds.map(id => getExamination(id)));
          const examMap: Record<string, any> = {};
          exams.forEach(e => { examMap[e.id] = e; });
          setLinkedExams(examMap);
        }

        // Auto-switch to prescription tab if patient has this medication
        if (relevantMeds.length > 0) {
          setActiveTab('prescription');
        }
      }
    } catch (error) {
      console.error('Failed to load medication details:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleEdit = () => {
    if (isEditing) {
      setFormData(medication || {});
    }
    setIsEditing(!isEditing);
  };

  const handleSave = async () => {
    if (!medicationId) return;
    setSubmitting(true);
    try {
      const updated = await updateCatalogMedication(medicationId, formData);
      setMedication(updated);
      setIsEditing(false);
    } catch (error) {
      console.error('Failed to update medication:', error);
      alert(t('medications.failed_save'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReprocess = async () => {
    if (!medicationId) return;
    setReprocessing(true);
    try {
      const updated = await reprocessMedication(medicationId);
      setMedication(updated);
      setFormData(updated);
    } catch (error) {
      console.error('Failed to reprocess medication:', error);
      alert(t('common.error'));
    } finally {
      setReprocessing(false);
    }
  };

  const handleAddSideEffect = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSideEffect.trim()) return;
    const currentEffects = formData.side_effects || [];
    if (!currentEffects.includes(newSideEffect.trim())) {
      setFormData({
        ...formData,
        side_effects: [...currentEffects, newSideEffect.trim()]
      });
    }
    setNewSideEffect('');
  };

  const handleRemoveSideEffect = (effect: string) => {
    setFormData({
      ...formData,
      side_effects: (formData.side_effects || []).filter(e => e !== effect)
    });
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
        <p className="text-gray-500 animate-pulse">Loading medication details...</p>
      </div>
    );
  }

  if (!medication) {
    return (
      <div className="text-center py-20">
        <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">{t('medications.no_medications_found')}</h2>
        <button 
          onClick={() => navigate('/medications')}
          className="mt-4 text-blue-600 hover:underline flex items-center justify-center mx-auto"
        >
          <ChevronLeft className="w-4 h-4 mr-1" />
          {t('medications.back_to_catalog')}
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <PageHeader
        title={medication.name}
        subtitle={
          <div className="flex items-center space-x-2">
            <p className="text-sm text-gray-500 dark:text-dark-muted font-medium">{t('medications.medication_id')}: {medication.id}</p>
            {medication.is_custom && (
              <span className="px-2 py-0.5 bg-amber-50 dark:bg-amber-900/20 rounded text-[10px] font-black uppercase text-amber-600 dark:text-amber-400 border border-amber-100 dark:border-amber-800/30">
                {t('medications.custom_resource')}
              </span>
            )}
          </div>
        }
        icon={<Pill className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('medications.catalog_title'), path: '/catalogs?type=medication' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-3">
            {activeTab === 'info' && (
              <>
                {isEditing ? (
                  <>
                    <button onClick={handleToggleEdit} className="px-4 py-2 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 transition-all font-bold text-sm flex items-center space-x-2">
                      <X className="w-4 h-4" /> <span>{t('common.cancel')}</span>
                    </button>
                    <button onClick={handleSave} disabled={submitting} className="px-6 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-all shadow-lg shadow-emerald-200/50 font-bold text-sm active:scale-95 flex items-center space-x-2">
                      <Save className="w-4 h-4" /> <span>{t('common.save')}</span>
                    </button>
                  </>
                ) : (
                  <>
                    <button onClick={handleReprocess} disabled={reprocessing} className="px-4 py-2 bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-100 dark:border-indigo-900/30 text-indigo-600 dark:text-indigo-400 rounded-xl hover:bg-indigo-100 transition-all font-semibold shadow-sm active:scale-95 text-sm flex items-center space-x-2">
                      <Sparkles className="w-4 h-4" /> <span>{reprocessing ? t('medications.reprocessing') : t('medications.ai_reprocess')}</span>
                    </button>
                    <button onClick={handleToggleEdit} className="px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-[#1a2b4b] dark:text-dark-text rounded-xl hover:bg-gray-50 transition-all font-semibold shadow-sm text-sm flex items-center space-x-2">
                      <Edit2 className="w-4 h-4" /> <span>{t('common.edit')}</span>
                    </button>
                  </>
                )}
              </>
            )}
            <a
              href={`/catalogs?type=medication&item=${medicationId}`}
              className="p-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-gray-400 hover:text-purple-600 transition-all shadow-sm"
              title="Manage in Catalogs"
            >
              <Database className="w-5 h-5" />
            </a>
          </div>
        }
      />

      {/* Tabs */}
      <div ref={tabsRef} className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl w-fit mb-8 border border-gray-200 dark:border-dark-border scroll-mt-32">
        <button 
          onClick={() => setActiveTab('info')}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'info' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
        >
          <Info className="w-4 h-4" />
          <span>{t('medications.general_info')}</span>
        </button>
        
        {currentPatient && (
          <button 
            onClick={() => setActiveTab('prescription')}
            className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'prescription' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
          >
            <Calendar className="w-4 h-4" />
            <span>{t('medications.my_prescription')}</span>
          </button>
        )}

        <button 
          onClick={() => setActiveTab('management')}
          className={`flex items-center space-x-2 px-6 py-2.5 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${activeTab === 'management' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
        >
          <Users className="w-4 h-4" />
          <span>{t('medications.management')}</span>
        </button>
      </div>

      <div className="grid grid-cols-1 gap-8">
        {/* INFO TAB */}
        {activeTab === 'info' && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 animate-in fade-in duration-500">
            <div className="xl:col-span-2 space-y-6">
              <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-8 shadow-sm">
                <h3 className="text-lg font-black text-[#1a2b4b] dark:text-dark-text mb-6 flex items-center uppercase tracking-tight">
                  <Info className="w-5 h-5 mr-3 text-blue-500" />
                  {t('medications.description')}
                </h3>
                <div className="prose dark:prose-invert max-w-none">
                  {isEditing ? (
                    <RichTextEditor value={formData.description || ''} onChange={(val) => setFormData({...formData, description: val})} placeholder={t('medications.description_placeholder')} minHeight="300px" />
                  ) : (
                    <div className="text-gray-700 dark:text-dark-text leading-relaxed text-lg font-medium">
                      {!medication.description ? (
                        <p className="italic text-gray-400">{t('medications.no_description')}</p>
                      ) : (medication.description.includes('</') || medication.description.includes('<br')) ? (
                        <div dangerouslySetInnerHTML={{ __html: medication.description }} />
                      ) : (
                        <ReactMarkdown>{medication.description}</ReactMarkdown>
                      )}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mt-12">
                  <div className="space-y-4">
                    <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em] flex items-center px-1">
                      <Stethoscope className="w-3.5 h-3.5 mr-2 text-emerald-500" />
                      {t('medications.indications')}
                    </h4>
                    <div className="p-6 bg-emerald-50/30 dark:bg-emerald-900/10 border border-emerald-100/50 dark:border-emerald-900/30 rounded-3xl">
                      {isEditing ? (
                        <textarea className="w-full bg-transparent border-none outline-none text-sm text-emerald-800 dark:text-emerald-300 resize-none h-32" value={formData.indications || ''} onChange={e => setFormData({...formData, indications: e.target.value})} placeholder={t('medications.indications_placeholder')} />
                      ) : (
                        <p className="text-emerald-800 dark:text-emerald-400 font-bold leading-relaxed">{medication.indications || t('medications.no_indications')}</p>
                      )}
                    </div>
                  </div>

                  <div className="space-y-4">
                    <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em] flex items-center px-1">
                      <AlertTriangle className="w-3.5 h-3.5 mr-2 text-amber-500" />
                      {t('medications.contraindications')}
                    </h4>
                    <div className="p-6 bg-amber-50/30 dark:bg-amber-900/10 border border-amber-100/50 dark:border-amber-900/30 rounded-3xl">
                      {isEditing ? (
                        <textarea className="w-full bg-transparent border-none outline-none text-sm text-amber-800 dark:text-amber-300 resize-none h-32" value={formData.contraindications || ''} onChange={e => setFormData({...formData, contraindications: e.target.value})} placeholder={t('medications.contraindications_placeholder')} />
                      ) : (
                        <p className="text-amber-800 dark:text-amber-400 font-bold leading-relaxed">{medication.contraindications || t('medications.no_contraindications')}</p>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-8">
               <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
                  <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em] mb-6 flex items-center">
                    <Users className="w-4 h-4 mr-2 text-rose-500" />
                    {t('medications.known_side_effects')}
                  </h4>
                  <div className="space-y-4">
                    <div className="flex flex-wrap gap-2">
                      {(isEditing ? formData.side_effects : medication.side_effects)?.map((effect, idx) => (
                        <span key={idx} className="px-3 py-1.5 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400 rounded-xl text-xs font-bold border border-rose-100 dark:border-rose-900/30 flex items-center group">
                          {effect}
                          {isEditing && (
                            <button onClick={() => handleRemoveSideEffect(effect)} className="ml-2 p-0.5 hover:bg-rose-200 rounded-full transition-colors"><X className="w-3 h-3" /></button>
                          )}
                        </span>
                      ))}
                    </div>
                    {isEditing && (
                      <form onSubmit={handleAddSideEffect} className="flex items-center space-x-2 mt-4">
                        <input type="text" className="flex-1 px-4 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-xs outline-none focus:ring-2 focus:ring-blue-500" placeholder={t('medications.add_side_effect_placeholder')} value={newSideEffect} onChange={e => setNewSideEffect(e.target.value)} />
                        <button type="submit" className="p-2 bg-blue-100 text-blue-600 rounded-xl hover:bg-blue-200 transition-colors"><Plus className="w-4 h-4" /></button>
                      </form>
                    )}
                  </div>
               </div>

               <div className="bg-blue-50/50 dark:bg-blue-900/10 rounded-[2rem] p-8 border border-blue-100/50 dark:border-blue-900/20">
                  <h4 className="text-[10px] font-black text-blue-600 uppercase tracking-[0.2em] mb-4 flex items-center">
                    <Clock className="w-4 h-4 mr-2" />
                    {t('medications.dosage_info')}
                  </h4>
                  <div className="prose prose-sm dark:prose-invert">
                    {isEditing ? (
                      <textarea className="w-full bg-white dark:bg-dark-surface border border-blue-200 dark:border-blue-800 rounded-xl p-4 text-xs resize-none h-32" value={formData.dosage_info || ''} onChange={e => setFormData({...formData, dosage_info: e.target.value})} placeholder={t('medications.dosage_placeholder')} />
                    ) : (
                      <p className="text-blue-900 dark:text-blue-300 font-bold">{medication.dosage_info || t('medications.dosage_guidelines_not_available')}</p>
                    )}
                  </div>
               </div>
            </div>
          </div>
        )}

        {/* PRESCRIPTION TAB */}
        {activeTab === 'prescription' && (
          <div className="space-y-8 animate-in slide-in-from-bottom-4 duration-500">
            {patientRecords.length === 0 ? (
               <div className="py-20 text-center bg-gray-50 dark:bg-dark-bg/30 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
                  <Bell className="w-16 h-16 text-gray-200 mx-auto mb-6" />
                  <h4 className="text-lg font-bold text-gray-500">{t('medications.no_active_prescription')}</h4>
                  <p className="text-gray-400 text-sm mt-2">{t('medications.add_to_your_records_desc')}</p>
                  <button 
                    onClick={() => navigate(`/patients/${currentPatient?.id}`)}
                    className="mt-6 px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50"
                  >
                    Go to Patient Record
                  </button>
               </div>
            ) : (
              <>
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
                  <div className="xl:col-span-2 space-y-6">
                    <h3 className="text-xs font-black text-gray-400 uppercase tracking-[0.3em] px-1">{t('medications.calendar.schedule_for')} {medication.name}</h3>
                    <UniversalCalendar events={staticCalendarEvents} defaultView="classic" />
                  </div>

                  <div className="space-y-6">
                    <h3 className="text-xs font-black text-gray-400 uppercase tracking-[0.3em] px-1">{t('medications.active_prescription')}</h3>
                    {patientRecords.filter(r => r.status?.toLowerCase() === 'active').map(record => (
                      <div key={record.id} className="bg-white dark:bg-dark-surface rounded-[2rem] border border-gray-100 dark:border-dark-border p-8 shadow-sm">
                        <div className="flex items-center justify-between mb-8">
                           <span className="px-3 py-1 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 text-[10px] font-black uppercase tracking-widest rounded-full border border-emerald-100 dark:border-emerald-800/30">
                             {record.status}
                           </span>
                           <Clock className="w-5 h-5 text-gray-300" />
                        </div>
                        
                        <div className="space-y-6">
                          <div>
                            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1">{t('medications.dosage')}</p>
                            <p className="text-2xl font-black text-gray-900 dark:text-dark-text tracking-tight">{record.dosage || 'N/A'}</p>
                          </div>
                          
                          <div>
                            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-1">{t('medications.frequency')}</p>
                            <p className="text-lg font-bold text-blue-600 dark:text-blue-400">{record.frequency?.display || record.frequency?.type || 'N/A'}</p>
                          </div>

                          <div className="pt-6 border-t border-gray-50 dark:border-dark-border">
                             <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-3">{t('medications.started')}</p>
                             <div className="flex items-center space-x-3 text-gray-700 dark:text-dark-text">
                               <Calendar className="w-5 h-5 text-gray-400" />
                               <span className="font-bold">{record.start_date ? new Date(record.start_date).toLocaleDateString() : 'N/A'}</span>
                             </div>
                          </div>
                        </div>
                      </div>
                    ))}

                    <div className="bg-indigo-50/50 dark:bg-indigo-900/10 rounded-[2rem] p-8 border border-indigo-100/50 dark:border-indigo-900/20">
                      <h4 className="text-[10px] font-black text-indigo-600 uppercase tracking-[0.2em] mb-6 flex items-center">
                        <Activity className="w-4 h-4 mr-2" />
                        {t('medications.related_examinations')}
                      </h4>
                      <div className="space-y-4">
                        {patientRecords.map(record => record.examination_id && linkedExams[record.examination_id] && (
                          <Link 
                            to={`/examinations/${record.examination_id}`}
                            key={record.id}
                            className="flex items-center justify-between p-4 bg-white dark:bg-dark-surface rounded-2xl border border-indigo-100 dark:border-indigo-800/30 hover:border-indigo-400 transition-all group"
                          >
                            <div className="flex items-center space-x-3">
                              <div className="p-2 bg-indigo-50 dark:bg-indigo-900/30 rounded-lg text-indigo-600">
                                <ClipboardList className="w-4 h-4" />
                              </div>
                              <div>
                                <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase">{linkedExams[record.examination_id].category || 'Visit'}</p>
                                <p className="text-[10px] text-gray-400 font-medium">{new Date(linkedExams[record.examination_id].examination_date).toLocaleDateString()}</p>
                              </div>
                            </div>
                            <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-600 transition-colors" />
                          </Link>
                        ))}
                        {patientRecords.every(r => !r.examination_id) && (
                          <p className="text-xs text-gray-400 italic text-center py-4">{t('medications.no_linked_exams')}</p>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border p-8">
                  <h3 className="text-xs font-black text-gray-400 uppercase tracking-[0.3em] mb-8">{t('medications.medication_history')}</h3>
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-50 dark:divide-dark-border">
                       <thead>
                         <tr>
                           <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('medications.start_date')}</th>
                           <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('medications.dosage')}</th>
                           <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('medications.status')}</th>
                           <th className="px-6 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('medications.indication')}</th>
                         </tr>
                       </thead>
                       <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                          {patientRecords.map(record => (
                            <tr key={record.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors">
                               <td className="px-6 py-5 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text">
                                 {record.start_date ? new Date(record.start_date).toLocaleDateString() : 'N/A'}
                               </td>
                               <td className="px-6 py-5 whitespace-nowrap text-sm font-black text-blue-600">
                                 {record.dosage || 'N/A'}
                               </td>
                               <td className="px-6 py-5 whitespace-nowrap">
                                  <span className={`px-2 py-1 rounded-lg text-[9px] font-black uppercase tracking-tighter border ${
                                    record.status === 'active' ? 'bg-emerald-50 text-emerald-700 border-emerald-100' : 'bg-gray-50 text-gray-500 border-gray-100'
                                  }`}>
                                    {record.status}
                                  </span>
                               </td>
                               <td className="px-6 py-5 text-xs text-gray-500 dark:text-dark-muted font-medium italic">
                                 {record.reason || 'No indication recorded'}
                               </td>
                            </tr>
                          ))}
                       </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* MANAGEMENT TAB */}
        {activeTab === 'management' && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 animate-in fade-in duration-500">
            <div className="xl:col-span-2 space-y-6">
               <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border overflow-hidden shadow-sm">
                  <div className="p-8 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
                    <h3 className="text-lg font-black text-[#1a2b4b] dark:text-dark-text uppercase tracking-tight flex items-center">
                      <Users className="w-5 h-5 mr-3 text-purple-500" />
                      {t('medications.patient_usage')}
                    </h3>
                    <div className="px-4 py-1.5 bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 rounded-xl text-xs font-black uppercase tracking-widest border border-purple-100 dark:border-purple-800/30">
                       {usage.length} {t('medications.active_patients')}
                    </div>
                  </div>
                  
                  <div className="divide-y divide-gray-50 dark:divide-dark-border">
                    {usage.length > 0 ? (
                      usage.map((item) => (
                        <div 
                          key={item.medication.id}
                          className="flex items-center justify-between p-8 hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 cursor-pointer transition-all group"
                          onClick={() => navigate(`/patients/${item.patient.id}`)}
                        >
                          <div className="flex items-center space-x-6">
                            <div className="w-14 h-14 bg-purple-50 dark:bg-purple-900/30 rounded-2xl flex items-center justify-center text-purple-600 border border-purple-100 dark:border-purple-800/30 shadow-sm">
                              <User className="w-6 h-6" />
                            </div>
                            <div>
                              <p className="text-lg font-black text-gray-900 dark:text-dark-text group-hover:text-blue-600 transition-colors">
                                {item.patient.name?.given?.join(' ')} {item.patient.name?.family}
                              </p>
                              <p className="text-xs text-gray-400 font-mono uppercase font-black tracking-widest mt-1">
                                MRN: {item.patient.mrn || 'N/A'}
                              </p>
                            </div>
                          </div>
                          <div className="text-right">
                            <div className="flex items-center justify-end space-x-2 mb-1">
                               <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('medications.prescribed')}</span>
                               <span className="text-sm font-bold text-gray-900 dark:text-dark-text">{item.medication.start_date || 'N/A'}</span>
                            </div>
                            <p className="text-xs text-blue-600 dark:text-blue-400 font-black uppercase tracking-widest">
                              {item.medication.dosage || 'Standard Dose'}
                            </p>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="py-20 text-center opacity-30">
                        <Users className="w-16 h-16 mx-auto mb-4" />
                        <p className="text-sm font-black uppercase tracking-[0.2em]">{t('medications.no_patients_prescribed')}</p>
                      </div>
                    )}
                  </div>
               </div>
            </div>

            <div className="space-y-6">
               <MedicationReminders medicationId={medicationId!} medicationName={medication.name} />
               
               <div className="bg-rose-50/50 dark:bg-rose-900/10 rounded-[2rem] p-8 border border-rose-100/50 dark:border-rose-900/20">
                  <h4 className="text-[10px] font-black text-rose-600 uppercase tracking-[0.2em] mb-6 flex items-center">
                    <Trash2 className="w-4 h-4 mr-2" />
                    {t('medications.administrative_actions')}
                  </h4>
                  <p className="text-xs text-rose-700 dark:text-rose-300 font-medium mb-6 leading-relaxed">
                    {t('medications.management_actions_desc')}
                  </p>
                  <button className="w-full py-3 bg-white dark:bg-dark-surface border border-rose-200 dark:border-rose-800 text-rose-600 rounded-xl font-bold text-xs uppercase tracking-widest hover:bg-rose-600 hover:text-white transition-all shadow-sm">
                    {t('medications.discontinue_globally')}
                  </button>
               </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default MedicationDetail;
