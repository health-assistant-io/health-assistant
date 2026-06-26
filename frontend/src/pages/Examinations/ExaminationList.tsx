import { useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { 
  Search, Plus, ChevronDown, 
  Calendar, Trash2, Info, X, 
  Stethoscope, CheckSquare, Square, Settings
} from 'lucide-react';
import { getExaminations, getExaminationDocuments, getExaminationStatus, getExamination, getExaminationCategories, bulkDeleteExaminations, getCachedExaminations } from '../../services/examinationService';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { AuthenticatedImageViewer } from '../../components/ui/AuthenticatedImageViewer';
import { AuthenticatedDicomViewer } from '../../components/ui/AuthenticatedDicomViewer';
import { AuthenticatedPdfViewer } from '../../components/ui/AuthenticatedPdfViewer';
import { AuthenticatedTextViewer } from '../../components/ui/AuthenticatedTextViewer';
import { getDocumentDownloadUrl } from '../../services/documentService';
import { DynamicIcon } from '../../components/ui/DynamicIcon';
import { ExaminationCard } from '../../components/examinations/ExaminationCard';
import { getExamCategory } from '../../utils/examinationUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { MasterDetailLayout } from '../../components/ui/MasterDetailLayout';
import { useMasterDetail } from '../../hooks/useMasterDetail';
import { CategoryDropdown } from '../../components/ui/CategoryDropdown';
import { ExaminationPreview } from '../../components/examinations/ExaminationPreview';
import { PageContainer } from '../../components/ui/PageContainer';
import { DatePicker } from '../../components/ui/DatePicker';

function ExaminationList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [selectedCategories, setSelectedCategories] = useState<string[]>(['All']);
  const [dateFilter, setDateFilter] = useState('All Time');
  const [customRange, setCustomRange] = useState({ start: '', end: '' });
  const [examinations, setExaminations] = useState<any[]>([]);
  const [selectedExam, setSelectedExam] = useState<any>(null);
  const [examDocuments, setExamDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [offset, setOffset] = useState(0);
  const [dbCategories, setDbCategories] = useState<any[]>([]);
  const LIMIT = 15;
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const setCurrentExaminationId = useUIStore(state => state.setCurrentExaminationId);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [viewerDoc, setViewerDoc] = useState<any>(null);
  const [dicomViewerDoc, setDicomViewerDoc] = useState<any>(null);
  const [pdfViewerDoc, setPdfViewerDoc] = useState<any>(null);
  const [textViewerDoc, setTextViewerDoc] = useState<any>(null);
  const [selectedInfo, setSelectedInfo] = useState<any>(null);
  const [isEditMode, setIsEditMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isDeleting, setIsDeleting] = useState(false);
  const lastSelectedId = useRef<string | null>(null);
  const hasUserSelected = useRef<boolean>(false);

  const { isLargeScreen, handleItemClick, containerRef } = useMasterDetail({
    detailPath: (id) => `/examinations/${id}`,
    onSelect: (id) => {
      const exam = examinations.find(e => e.id === id);
      if (exam) handleSelectExam(exam);
    }
  });
  
  const toggleSelectAll = () => {
    if (selectedIds.length === filteredExaminations.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredExaminations.map(e => e.id));
    }
  };

  const toggleSelectOne = (id: string) => {
    if (selectedIds.includes(id)) {
      setSelectedIds(selectedIds.filter(i => i !== id));
    } else {
      setSelectedIds([...selectedIds, id]);
    }
  };

  const handleBulkDelete = () => {
    if (selectedIds.length === 0) return;
    
    showConfirmation({
      title: t('common.delete') + ' ' + t('common.examinations'),
      message: t('examinations.delete_confirm', { count: selectedIds.length }),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        setIsDeleting(true);
        try {
          await bulkDeleteExaminations(selectedIds);
          await fetchExams();
          setSelectedIds([]);
          setIsEditMode(false);
        } catch (error) {
          console.error("Failed to delete examinations", error);
        } finally {
          setIsDeleting(false);
        }
      }
    });
  };

  const fetchExams = async (isInitial = true) => {
    if (isInitial) {
      setOffset(0);
      
      if (currentPatient?.id) {
        const cached = await getCachedExaminations(currentPatient.id);
        if (cached && cached.length > 0) {
          setExaminations(cached);
          
          if (!lastSelectedId.current && isLargeScreen) {
             lastSelectedId.current = cached[0].id;
             setSelectedExam(cached[0]);
          }
          setLoading(false); 
        } else {
          setLoading(true);
        }
      } else {
        setLoading(true);
      }
    } else {
      setLoadingMore(true);
    }

    try {
      const currentOffset = isInitial ? 0 : offset;
      const [data, cats] = await Promise.all([
        getExaminations(currentPatient?.id, LIMIT, currentOffset),
        getExaminationCategories() 
      ]);
      
      if (cats && cats.length > 0) {
        setDbCategories(cats);
      }

      if (isInitial) {
        setExaminations(data);
        if (data.length > 0) {
          const currentId = lastSelectedId.current;
          const isFirstInNewData = data[0].id === currentId;
          
          if (isLargeScreen && !hasUserSelected.current && (!currentId || !isFirstInNewData)) {
            setSelectedExam(data[0]);
            lastSelectedId.current = data[0].id;
            
            try {
              const fullExam = await getExamination(data[0].id);
              if (lastSelectedId.current === data[0].id) {
                setSelectedExam(fullExam);
              }
            } catch (err) {
              console.error("Failed to fetch full exam details", err);
            }
          } else if (currentId) {
            const stillExists = data.find((e: any) => e.id === currentId);
            if (stillExists) {
              try {
                const fullExam = await getExamination(currentId);
                if (lastSelectedId.current === currentId) {
                  setSelectedExam(fullExam);
                }
              } catch (err) {
                console.error("Failed to sync exam details", err);
              }
            }
          }
        } else {
          setSelectedExam(null);
          lastSelectedId.current = null;
          hasUserSelected.current = false;
        }
      } else {
        setExaminations((prev: any[]) => {
           const existingIds = new Set(prev.map(e => e.id));
           const newData = data.filter((e: any) => !existingIds.has(e.id));
           return [...prev, ...newData];
        });
      }

      setHasMore(data.length === LIMIT);
      if (!isInitial) setOffset((prev: number) => prev + LIMIT);

      if (isInitial && data.length > 0 && currentPatient?.id) {
         setTimeout(() => {
           getExaminations(currentPatient.id, 100, LIMIT).catch(e => console.log("Background sync paused", e));
         }, 2000);
      }

    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  const handleLoadMore = () => {
    if (!loadingMore && hasMore) {
      const nextOffset = offset + LIMIT;
      setOffset(nextOffset);
      fetchExams(false);
    }
  };

  const handleSelectExam = async (exam: any) => {
    if (lastSelectedId.current === exam.id) return;
    hasUserSelected.current = true;
    lastSelectedId.current = exam.id;
    setSelectedExam(exam);
    
    try {
      const fullExam = await getExamination(exam.id);
      if (lastSelectedId.current === exam.id) {
         setSelectedExam(fullExam);
      }
    } catch (err) {
      console.error("Failed to fetch full examination details", err);
    }
  };


  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  useEffect(() => {
    if (selectedExam?.id) {
      setCurrentExaminationId(selectedExam.id);
    } else {
      setCurrentExaminationId(null);
    }
    return () => {
      setCurrentExaminationId(null);
    };
  }, [selectedExam?.id, setCurrentExaminationId]);

  useEffect(() => {
    setSearchTerm('');
    setSelectedCategories(['All']);
    setDateFilter('All Time');
    setCustomRange({ start: '', end: '' });
    setOffset(0);
    setHasMore(true);
    setExaminations([]);
    setSelectedExam(null);
    lastSelectedId.current = null;
    hasUserSelected.current = false;
    
    fetchExams(true);
  }, [currentPatient?.id]);

  useEffect(() => {
    const fetchDocs = async () => {
      if (selectedExam?.id) {
        try {
          const docs = await getExaminationDocuments(selectedExam.id);
          setExamDocuments(docs);
        } catch (err) {
          console.error(err);
        }
      } else {
        setExamDocuments([]);
      }
    };
    fetchDocs();
  }, [selectedExam?.id]);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    
    const processingExams = examinations.filter(e => 
      e.extraction_status && !['completed', 'failed'].includes(e.extraction_status)
    );

    const isAnyDocProcessing = examDocuments.some(doc => 
      ['processing', 'uploaded'].includes(doc.status)
    );

    if (processingExams.length > 0 || isAnyDocProcessing) {
      interval = setInterval(async () => {
        try {
          const idsToPoll = new Set(processingExams.map(e => e.id));
          if (selectedExam?.id) idsToPoll.add(selectedExam.id);

          const updates = await Promise.all(Array.from(idsToPoll).map(id => getExaminationStatus(id)));
          
          setExaminations((prevExams: any[]) => prevExams.map((exam: any) => {
            const statusData = updates.find(u => u.id === exam.id);
            if (!statusData) return exam;
            
            if (exam.extraction_status === statusData.extraction_status && 
                exam.extraction_progress === statusData.extraction_progress &&
                exam.error_message === statusData.error_message &&
                JSON.stringify(exam.document_statuses) === JSON.stringify(statusData.documents)) {
              return exam;
            }

            return {
              ...exam,
              extraction_status: statusData.extraction_status,
              extraction_progress: statusData.extraction_progress,
              error_message: statusData.error_message,
              document_statuses: statusData.documents
            };
          }));

          const selectedUpdate = updates.find(u => u.id === selectedExam?.id);
          if (selectedUpdate) {
            setSelectedExam((prev: any) => {
              if (!prev || prev.id !== selectedUpdate.id) return prev;
              if (prev.extraction_status === selectedUpdate.extraction_status && 
                  prev.extraction_progress === selectedUpdate.extraction_progress) return prev;
              return {
                ...prev,
                extraction_status: selectedUpdate.extraction_status,
                extraction_progress: selectedUpdate.extraction_progress,
                error_message: selectedUpdate.error_message
              };
            });

            setExamDocuments((prevDocs: any[]) => {
              let changed = false;
              const nextDocs = prevDocs.map((doc: any) => {
                const ds = selectedUpdate.documents.find((d: any) => d.id === doc.id);
                if (ds && (doc.status !== ds.status || doc.progress !== ds.progress)) {
                  changed = true;
                  return { ...doc, status: ds.status, progress: ds.progress };
                }
                return doc;
              });
              return changed ? nextDocs : prevDocs;
            });
          }

          const justFinished = updates.filter(u => 
            !u.extraction_status || ['completed', 'failed'].includes(u.extraction_status)
          );

          if (justFinished.length > 0) {
             const anySelectedFinished = selectedUpdate && (!selectedUpdate.extraction_status || ['completed', 'failed'].includes(selectedUpdate.extraction_status));
             
             if (anySelectedFinished) {
                const fullDocs = await getExaminationDocuments(selectedExam.id);
                setExamDocuments(fullDocs);
                const fullExam = await getExamination(selectedExam.id);
                setSelectedExam(fullExam);
                setExaminations((prev: any[]) => prev.map(e => e.id === fullExam.id ? fullExam : e));
             }
             
             const otherFinished = justFinished.some(u => u.id !== selectedExam?.id);
             if (otherFinished) {
                const updatedList = await getExaminations(currentPatient?.id);
                setExaminations(updatedList);
             }
          }
        } catch (err) {
          console.error("Polling failed in list", err);
        }
      }, 3000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [
    examinations.filter(e => e.extraction_status && !['completed', 'failed'].includes(e.extraction_status)).length,
    selectedExam?.id,
    examDocuments.some(d => ['processing', 'uploaded'].includes(d.status)),
    currentPatient?.id
  ]);

  const sortedCategories = useMemo(() => {
    const baseList = dbCategories.length > 0 
      ? dbCategories.map(c => typeof c === 'string' ? c : c.name)
      : Array.from(new Set(examinations.map(e => getExamCategory(e)).filter(Boolean)));

    const unique = Array.from(new Set(baseList));
    const clinical = unique.filter(c => c !== 'Other' && c !== 'Unmapped Results');
    
    clinical.sort((a, b) => a.localeCompare(b));

    const result = [...clinical];
    if (unique.includes('Unmapped Results')) result.push('Unmapped Results');
    if (unique.includes('Other')) result.push('Other');
    
    return result;
  }, [dbCategories, examinations]);

  const tabsWithCounts = useMemo(() => {
    const counts: Record<string, number> = { 'All': examinations.length };
    
    examinations.forEach(exam => {
      const cat = getExamCategory(exam);
      if (cat) {
        counts[cat] = (counts[cat] || 0) + 1;
      }
    });

    const nonEmpty = sortedCategories.filter(cat => counts[cat] > 0);
    
    return [
      { name: t('common.view_all') as string, id: 'All', count: examinations.length, icon: null, color: null },
      ...nonEmpty.map(name => {
        const catObj = dbCategories.find(c => c.name === name);
        return {
          name: t(`categories.${name}`, name) as string, // Use i18n translation or fallback to name
          id: name,
          count: counts[name] || 0,
          icon: catObj?.icon || null,
          color: catObj?.color || null
        };
      })
    ];
  }, [sortedCategories, examinations, dbCategories, t]);

  const filteredExaminations = examinations.filter(e => {
    const notesToSearch = (e.notes || '').toLowerCase();
    const patientNotesToSearch = (e.patient_notes || '').toLowerCase();
    const searchLower = searchTerm.toLowerCase();
    
    const matchesSearch = notesToSearch.includes(searchLower) || patientNotesToSearch.includes(searchLower);
    const matchesTab = selectedCategories.includes('All') || selectedCategories.includes(getExamCategory(e));
    
    let matchesDate = true;
    const examDate = new Date(e.examination_date);
    const now = new Date();
    
    if (dateFilter === 'Last 7 Days') {
      const sevenDaysAgo = new Date();
      sevenDaysAgo.setDate(now.getDate() - 7);
      matchesDate = examDate >= sevenDaysAgo;
    } else if (dateFilter === 'Last 30 Days') {
      const thirtyDaysAgo = new Date();
      thirtyDaysAgo.setDate(now.getDate() - 30);
      matchesDate = examDate >= thirtyDaysAgo;
    } else if (dateFilter === 'Last 6 Months') {
      const sixMonthsAgo = new Date();
      sixMonthsAgo.setMonth(now.getMonth() - 6);
      matchesDate = examDate >= sixMonthsAgo;
    } else if (dateFilter === 'This Year') {
      const startOfYear = new Date(now.getFullYear(), 0, 1);
      matchesDate = examDate >= startOfYear;
    } else if (dateFilter === 'Custom Range') {
      const start = customRange.start ? new Date(customRange.start) : null;
      const end = customRange.end ? new Date(customRange.end) : null;
      if (start) matchesDate = matchesDate && examDate >= start;
      if (end) {
        const endOfDay = new Date(end);
        endOfDay.setHours(23, 59, 59, 999);
        matchesDate = matchesDate && examDate <= endOfDay;
      }
    }

    return matchesSearch && matchesTab && matchesDate;
  });

  const handleDocumentClick = async (doc: any) => {
    if (doc.filename.toLowerCase().endsWith('.dcm')) {
      setDicomViewerDoc(doc);
    } else if (doc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i)) {
      setViewerDoc(doc);
    } else if (doc.filename.match(/\.pdf$/i)) {
      setPdfViewerDoc(doc);
    } else if (doc.filename.match(/\.(txt|md)$/i)) {
      setTextViewerDoc(doc);
    } else {
      try {
        const url = await getDocumentDownloadUrl(doc.id);
        window.open(url, '_blank');
      } catch (err) {
        console.error("Failed to open document", err);
      }
    }
  };

  const toggleCategory = (category: string) => {
    setSelectedCategories(prev => {
      if (category === 'All') return ['All'];
      const filtered = prev.filter(c => c !== 'All');
      if (filtered.includes(category)) {
        const next = filtered.filter(c => c !== category);
        return next.length === 0 ? ['All'] : next;
      }
      return [...filtered, category];
    });
  };

  const ListHeader = (
    <div className="flex items-center space-x-2 w-full">
      {isEditMode && (
        <div className="flex items-center mr-1">
          <input 
            type="checkbox" 
            className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 transition-all cursor-pointer"
            checked={selectedIds.length === filteredExaminations.length && filteredExaminations.length > 0}
            onChange={toggleSelectAll}
          />
        </div>
      )}
      <h3 className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">{t('examinations.history_timeline')} ({filteredExaminations.length})</h3>
      <div className="ml-auto">
        { (searchTerm || !selectedCategories.includes('All') || dateFilter !== 'All Time') && (
          <button 
            onClick={() => {
              setSearchTerm('');
              setSelectedCategories(['All']);
              setDateFilter('All Time');
              setCustomRange({ start: '', end: '' });
            }}
            className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/30 text-[10px] font-bold text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 uppercase tracking-tight rounded-md transition-colors active:scale-95"
            title="Reset all filters"
          >
            {t('common.reset')}
          </button>
        )}
      </div>
    </div>
  );

  const List = (
        <div className="relative space-y-4">
          {loading ? (
             <LoadingState variant="mini" showText={true} message={t('examinations.syncing')} />
          ) : filteredExaminations.length === 0 ? (
             <p className="text-gray-500 dark:text-dark-muted text-sm">{t('examinations.no_exams')}</p>
          ) : filteredExaminations.map((exam) => (
            <ExaminationCard
              key={exam.id}
              examination={exam}
              isSelected={selectedExam?.id === exam.id}
              isEditMode={isEditMode}
              onSelectToggle={toggleSelectOne}
              categoryIconOnly={true}
              allowEventInteraction={false}
              onClick={() => {
                if (isEditMode) {
                  toggleSelectOne(exam.id);
                  return;
                }
                handleItemClick(exam.id, exam);
              }}
            />
          ))}

          {hasMore && (
            <div className="pt-4 pb-10 flex justify-center">
              <button 
                onClick={handleLoadMore}
                disabled={loadingMore}
                className="flex items-center space-x-2 px-6 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-xs font-bold text-gray-500 hover:bg-gray-50 dark:hover:bg-dark-bg transition-all active:scale-95 disabled:opacity-50"
              >
                {loadingMore ? (
              <div className="flex items-center space-x-2">
                <div className="w-3 h-3 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                <span>{t('common.loading', 'Loading...')}</span>
              </div>
            ) : (
              <div className="flex items-center space-x-2">
                <ChevronDown className="w-3 h-3" />
                <span>{t('examinations.load_more', 'Load more history')}</span>
              </div>
                )}
              </button>
            </div>
          )}
        </div>
  );

  const Preview = (
    <ExaminationPreview 
      selectedExam={selectedExam}
      examDocuments={examDocuments}
      onDocumentClick={handleDocumentClick}
      onInfoClick={setSelectedInfo}
    />
  );

  if (!currentPatient) {
    return <NoPatientState icon={Stethoscope} contextKey="examinations" />;
  }

  return (
    <PageContainer>
      <PageHeader
        title={t('examinations.title')}
        subtitle={t('examinations.subtitle')}
        icon={<Stethoscope className="w-8 h-8" />}
        breadcrumbs={[]}
        showBackButton={true}
      />

      <StickyToolbar
        className="flex-col sm:flex-row items-stretch sm:items-center"
        actions={
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full lg:w-auto flex-shrink-0 pt-2 sm:pt-0">
            {isEditMode && selectedIds.length > 0 && (
              <button 
                onClick={handleBulkDelete}
                disabled={isDeleting}
                className="flex items-center space-x-2 px-3 py-1.5 bg-red-50 dark:bg-red-900/10 text-red-600 dark:text-red-400 border border-red-100 dark:border-red-900/20 rounded-xl font-bold text-xs hover:bg-red-100 dark:hover:bg-red-900/20 transition-all active:scale-95 disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>{t('common.delete')} ({selectedIds.length})</span>
              </button>
            )}

            <button
              onClick={() => {
                setIsEditMode(!isEditMode);
                setSelectedIds([]);
              }}
              className={`flex items-center space-x-2 px-4 py-2.5 rounded-xl font-bold text-sm transition-all shadow-sm active:scale-95 border ${
                isEditMode 
                  ? 'bg-blue-600 text-white border-blue-700 shadow-lg shadow-blue-200/50' 
                  : 'bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-bg'
              }`}
            >
              {isEditMode ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
              <span className="hidden sm:inline">{isEditMode ? t('examinations.finish_editing') : t('examinations.edit_timeline')}</span>
            </button>

            <div className="relative w-full sm:w-64 hidden md:block">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Calendar className="h-4 w-4 text-gray-400" />
              </div>
              <select
                className="block w-full pl-10 pr-3 py-2 border border-gray-200 dark:border-dark-border rounded-xl leading-5 bg-white dark:bg-dark-surface dark:text-dark-text focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm appearance-none"
                value={dateFilter}
                onChange={(e) => setDateFilter(e.target.value)}
              >
        <option value="All Time">{t('common.all_time', 'All Time')}</option>
        <option value="Last 7 Days">{t('common.last_7_days', 'Last 7 Days')}</option>
        <option value="Last 30 Days">{t('common.last_30_days', 'Last 30 Days')}</option>
        <option value="Last 6 Months">{t('common.last_6_months', 'Last 6 Months')}</option>
        <option value="This Year">{t('common.this_year', 'This Year')}</option>
        <option value="Custom Range">{t('common.custom_range', 'Custom Range')}</option>
              </select>
              <div className="absolute inset-y-0 right-0 pr-2 flex items-center pointer-events-none">
                <ChevronDown className="h-4 w-4 text-gray-400" />
              </div>
            </div>

            <button 
              onClick={() => navigate('/examinations/upload')}
              className="w-full sm:w-auto flex items-center justify-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95 whitespace-nowrap"
            >
              <Plus className="w-4 h-4" />
              <span className="hidden lg:inline">{t('common.new_examination')}</span>
            </button>
          </div>
        }
      >
          <CategoryDropdown 
             tabs={tabsWithCounts} 
             selectedCategories={selectedCategories} 
             onToggleCategory={toggleCategory} 
             label={t('examinations.categories')}
             allLabel={t('common.view_all')}
          />
      </StickyToolbar>

      {dateFilter === 'Custom Range' && (
        <div className="flex flex-wrap items-center gap-4 mb-6 bg-gray-50 dark:bg-dark-bg p-4 rounded-xl border border-gray-100 dark:border-dark-border animate-in slide-in-from-top-2 duration-300">
          <div className="flex items-center space-x-2">
            <span className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase">{t('common.from', 'From:')}</span>
            <DatePicker 
              className="w-40 px-3 py-1.5 border border-gray-200 dark:border-dark-border rounded-lg text-sm focus-within:ring-1 focus-within:ring-blue-500 outline-none dark:bg-dark-surface dark:text-dark-text"
              value={customRange.start}
              onChange={(date) => setCustomRange({ ...customRange, start: date })}
              variant="unstyled"
            />
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase">{t('common.to', 'To:')}</span>
            <DatePicker 
              className="w-40 px-3 py-1.5 border border-gray-200 dark:border-dark-border rounded-lg text-sm focus-within:ring-1 focus-within:ring-blue-500 outline-none dark:bg-dark-surface dark:text-dark-text"
              value={customRange.end}
              onChange={(date) => setCustomRange({ ...customRange, end: date })}
              variant="unstyled"
            />
          </div>
          <button 
            onClick={() => {
              setCustomRange({ start: '', end: '' });
              setDateFilter('All Time');
            }}
            className="text-xs font-bold text-red-500 hover:text-red-700 uppercase"
          >
            {t('common.clear_filter', 'Clear Filter')}
          </button>
        </div>
      )}

      <MasterDetailLayout 
        list={List}
        listHeader={ListHeader}
        detail={Preview}
        listWidth="lg:w-[400px] xl:w-[500px]"
        containerRef={containerRef}
        showDetail={isLargeScreen}
      />
      
      {viewerDoc && (
        <AuthenticatedImageViewer 
          documentId={viewerDoc.id}
          filename={viewerDoc.filename}
          onClose={() => setViewerDoc(null)}
          onRefresh={fetchExams}
        />
      )}

      {dicomViewerDoc && (
        <AuthenticatedDicomViewer
          documentId={dicomViewerDoc.id}
          filename={dicomViewerDoc.filename}
          onClose={() => setDicomViewerDoc(null)}
          onRefresh={fetchExams}
          gallery={examDocuments}
        />
      )}

      {pdfViewerDoc && (
        <AuthenticatedPdfViewer 
          documentId={pdfViewerDoc.id}
          filename={pdfViewerDoc.filename}
          onClose={() => setPdfViewerDoc(null)}
        />
      )}

      {textViewerDoc && (
        <AuthenticatedTextViewer 
          documentId={textViewerDoc.id}
          filename={textViewerDoc.filename}
          onClose={() => setTextViewerDoc(null)}
        />
      )}

      {selectedInfo && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
           <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-[2.5rem] p-10 border border-gray-100 dark:border-dark-border relative shadow-2xl">
              <button onClick={() => setSelectedInfo(null)} className="absolute top-6 right-6 p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
              <div className="flex items-center space-x-4 mb-8">
                 <div className="p-3 bg-blue-50 dark:bg-blue-900/30 rounded-2xl">
                    <Info className="w-6 h-6 text-blue-600 dark:text-blue-400" />
                 </div>
                 <h2 className="text-2xl font-black text-gray-900 dark:text-dark-text uppercase tracking-tight">{selectedInfo.displayName}</h2>
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none text-gray-600 dark:text-dark-muted italic leading-relaxed">
                 <div dangerouslySetInnerHTML={{ __html: selectedInfo.info }} />
              </div>
           </div>
        </div>
      )}
    </PageContainer>
  );
}

export default ExaminationList;
