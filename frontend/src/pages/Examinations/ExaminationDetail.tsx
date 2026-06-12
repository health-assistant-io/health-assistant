import { useEffect, useState, useRef, useMemo } from 'react';
import { useParams, Link, useNavigate, useLocation } from 'react-router-dom';
import { getExaminationDocuments, getExamination, updateExamination, getExaminationCategories, createExaminationCategory, deleteExamination, extractExamination, getExaminationStatus } from '../../services/examinationService';
import { TaskProgressIndicator } from '../../components/ui/TaskProgressIndicator';
import { DynamicIcon } from '../../components/ui/DynamicIcon';
import { LoadingState } from '../../components/ui/LoadingState';
import { uploadDocument, getDocumentDownloadUrl, updateDocument } from '../../services/documentService';
import { offlineService } from '../../services/offlineService';
import { RichTextEditor } from '../../components/ui/RichTextEditor';
import ReactMarkdown from 'react-markdown';
import { AIBadge } from '../../components/ui/AIBadge';
import { 
  Edit2, Check, X, Trash2, LayoutGrid, List, Table as TableIcon,
  FileText, Image as ImageIcon, Activity, FlaskConical, 
  ClipboardList, Calendar, ArrowLeft,
  Download, ExternalLink, Search, Clock, Plus, Pill, Cpu,
  Bookmark, Stethoscope, BriefcaseMedical, ChevronDown, 
  RotateCcw, CloudLightning, Camera, Building2
} from 'lucide-react';
import { listDoctors, createDoctor, Doctor } from '../../services/doctorService';
import { deleteObservation } from '../../services/fhirService';
import { isAbnormal } from '../../utils/biomarkerUtils';
import { useUIStore } from '../../store/slices/uiSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { useBiomarkers } from '../../hooks/useBiomarkers';
import { useTabScroll } from '../../hooks/useTabScroll';
import { Biomarker } from '../../types/biomarker';
import { DoctorSelector } from '../../components/ui/DoctorSelector';
import { OrganizationSelector } from '../../components/ui/OrganizationSelector';
import { CategorySelector } from '../../components/ui/CategorySelector';
import { listOrganizations, createOrganization, Organization } from '../../services/organizationService';
import { BiomarkerList } from '../../components/biomarkers/BiomarkerList';

// Import visualizers
import { AuthenticatedThumbnail } from '../../components/ui/AuthenticatedThumbnail';
import { AuthenticatedImageViewer } from '../../components/ui/AuthenticatedImageViewer';
import { AuthenticatedDicomViewer } from '../../components/ui/AuthenticatedDicomViewer';
import { AuthenticatedPdfViewer } from '../../components/ui/AuthenticatedPdfViewer';
import { AuthenticatedTextViewer } from '../../components/ui/AuthenticatedTextViewer';
import { AddBiomarkerModal } from '../../components/examinations/AddBiomarkerModal';
import { ExaminationAIActions } from '../../components/ui/ExaminationAIActions';
import { MedicationAIActions } from '../../components/ui/MedicationAIActions';
import { AssociatedEvents } from '../../components/events/AssociatedEvents';
import MedicationCard from '../../components/medications/MedicationCard';

import { useTranslation } from 'react-i18next';
import { isMobileDevice } from '../../utils/deviceUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

const ExaminationDetail = () => {
  const { t } = useTranslation();
  const { examinationId, activeTab } = useParams<{ examinationId: string, activeTab?: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const setCurrentExaminationId = useUIStore(state => state.setCurrentExaminationId);
  const { showReferenceRanges } = useSettingsStore();
  
  const queryParams = new URLSearchParams(location.search);
  const highlightedId = queryParams.get('highlight');
  
  const [documents, setDocuments] = useState<any[]>([]);
  const [examination, setExamination] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [activeMainTab, setActiveMainTab] = useState<'overview' | 'biomarkers' | 'imaging' | 'documents'>((activeTab as any) || 'overview');
  const tabsRef = useRef<HTMLDivElement>(null);

  // Auto-scroll when tab changes
  useTabScroll(tabsRef, activeMainTab);
  
  // URL sync for tabs
  useEffect(() => {
    if (activeTab && ['overview', 'biomarkers', 'imaging', 'documents'].includes(activeTab)) {
      setActiveMainTab(activeTab as any);
    }
  }, [activeTab]);

  const handleTabChange = (tab: 'overview' | 'biomarkers' | 'imaging' | 'documents') => {
    setActiveMainTab(tab);
    // Keep search params (like highlight) when switching tabs if they exist
    const search = location.search;
    navigate(`/examinations/${examinationId}/${tab}${search}`, { replace: true });
  };
  const [docViewMode, setDocViewMode] = useState<'list' | 'grid'>('list');
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null);
  const [biomarkerPerspective, setBiomarkerPerspective] = useState<'technical' | 'clinical'>('technical');
  const [biomarkerViewMode, setBiomarkerViewMode] = useState<'grid' | 'list' | 'table'>('table');
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  
  // Global editing toggle
  const [isGlobalEditing, setIsGlobalEditing] = useState(false);
  
  // Date editing state
  const [isEditingDate, setIsEditingDate] = useState(false);
  const [tempDate, setTempDate] = useState('');
  
  // Note editing states
  const [isEditingNotes, setIsEditingNotes] = useState(false);
  const [notesContent, setNotesContent] = useState('');
  const [isEditingPatientNotes, setIsEditingPatientNotes] = useState(false);
  const [patientNotesContent, setPatientNotesContent] = useState('');
  
  // Draft states
  const [hasNotesDraft, setHasNotesDraft] = useState(false);
  const [hasPatientNotesDraft, setHasPatientNotesDraft] = useState(false);
  
  // Category & Doctor editing states
  const [tempCategory, setTempCategory] = useState('');
  const [isAnalysisDropdownOpen, setIsAnalysisDropdownOpen] = useState(false);
  const [dynamicCategories, setDynamicCategories] = useState<any[]>([]);
  const [availableDoctors, setAvailableDoctors] = useState<Doctor[]>([]);
  const [availableOrganizations, setAvailableOrganizations] = useState<Organization[]>([]);
  const [selectedDoctorIds, setSelectedDoctorIds] = useState<string[]>([]);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Record<string, Biomarker>>({});
  const [isAddBiomarkerOpen, setIsAddBiomarkerOpen] = useState(false);
  const [biomarkerDataMode, setBiomarkerDataMode] = useState<'raw' | 'normalized'>('normalized');
  
  // Modal viewers
  const [viewerDoc, setViewerDoc] = useState<any>(null);
  const [dicomViewerDoc, setDicomViewerDoc] = useState<any>(null);
  const [pdfViewerDoc, setPdfViewerDoc] = useState<any>(null);
  const [textViewerDoc, setTextViewerDoc] = useState<any>(null);
  

  // Pipeline states
  const [pollStartTime, setPollStartTime] = useState<number | null>(null);
  const [isStalled, setIsStalled] = useState(false);
  const [isFromCache, setIsFromCache] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const analysisDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (analysisDropdownRef.current && !analysisDropdownRef.current.contains(event.target as Node)) {
        setIsAnalysisDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);


  const { getGroupedData, biomarkers: allExamBiomarkers } = useBiomarkers({ 
    documents, 
    observations: examination?.observations 
  });
  const biomarkerGroups = getGroupedData(biomarkerPerspective);

  // Imaging specific docs
  const imagingDocs = documents.filter(d => 
    d.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i) || 
    (d.entities?.document_category || '').toLowerCase().includes('imaging')
  );

  const augmentedKeyBiomarkers = useMemo(() => {
    // Get top 6 biomarkers: prioritized by Abnormal status
    const keyOnes = [...allExamBiomarkers]
      .sort((a, b) => {
         const aAbnormal = isAbnormal(a.interpretation);
         const bAbnormal = isAbnormal(b.interpretation);
         
         if (aAbnormal && !bAbnormal) return -1;
         if (!aAbnormal && bAbnormal) return 1;
         return 0;
      })
      .slice(0, 6);

    return keyOnes.map(b => {
      if (b.slug && catalog[b.slug]) {
        return { ...b, info: catalog[b.slug].info };
      }
      return b;
    });
  }, [allExamBiomarkers, catalog]);

  useEffect(() => {
    setIsPageSearchSupported(activeTab === 'documents');
    if (activeTab !== 'documents') setSearchTerm('');
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [activeTab, setIsPageSearchSupported, setSearchTerm]);

  useEffect(() => {
    if (examinationId) {
      setCurrentExaminationId(examinationId);
      fetchData();
      checkDrafts();
    }
    return () => {
      setCurrentExaminationId(null);
    };
  }, [examinationId]);

  useEffect(() => {
    if (!loading && highlightedId && activeMainTab === 'biomarkers') {
      const element = document.getElementById(`biomarker-${highlightedId}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [loading, highlightedId, activeMainTab]);

  const checkDrafts = async () => {
    if (!examinationId) return;
    const notesDraft = await offlineService.getDraft(`${examinationId}-notes`);
    const patientNotesDraft = await offlineService.getDraft(`${examinationId}-patient-notes`);
    
    if (notesDraft) setHasNotesDraft(true);
    if (patientNotesDraft) setHasPatientNotesDraft(true);
  };

  const applyNotesDraft = async () => {
    if (!examinationId) return;
    const draft = await offlineService.getDraft(`${examinationId}-notes`);
    if (draft) {
      setNotesContent(draft.data);
      setIsEditingNotes(true);
      setHasNotesDraft(false);
    }
  };

  const applyPatientNotesDraft = async () => {
    if (!examinationId) return;
    const draft = await offlineService.getDraft(`${examinationId}-patient-notes`);
    if (draft) {
      setPatientNotesContent(draft.data);
      setIsEditingPatientNotes(true);
      setHasPatientNotesDraft(false);
    }
  };

  // Auto-save drafts
  useEffect(() => {
    if (isEditingNotes && examinationId) {
      const timer = setTimeout(() => {
        offlineService.saveDraft(`${examinationId}-notes`, 'note', notesContent);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [notesContent, isEditingNotes, examinationId]);

  useEffect(() => {
    if (isEditingPatientNotes && examinationId) {
      const timer = setTimeout(() => {
        offlineService.saveDraft(`${examinationId}-patient-notes`, 'note', patientNotesContent);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [patientNotesContent, isEditingPatientNotes, examinationId]);

  // Polling for extraction status
  useEffect(() => {
    let interval: NodeJS.Timeout;
    
    // Check if anything is currently processing
    const isExamProcessing = examination && 
      examination.extraction_status && 
      !['completed', 'failed'].includes(examination.extraction_status);

    const isAnyDocProcessing = documents.some(doc => 
      ['processing', 'uploaded'].includes(doc.status)
    );

    const isProcessing = isExamProcessing || isAnyDocProcessing;

    if (isProcessing) {
      if (!pollStartTime) {
        setPollStartTime(Date.now());
      }
      
      interval = setInterval(async () => {
        try {
          const statusData = await getExaminationStatus(examinationId!);
          
          // Update examination status fields only to minimize traffic/re-renders
          setExamination((prev: any) => {
            if (!prev) return null;
            // Only update if changed
            if (prev.extraction_status === statusData.extraction_status && 
                prev.extraction_progress === statusData.extraction_progress &&
                prev.error_message === statusData.error_message) {
              return prev;
            }
            return { 
              ...prev, 
              extraction_status: statusData.extraction_status,
              extraction_progress: statusData.extraction_progress,
              error_message: statusData.error_message
            };
          });

          // Update documents status fields
          setDocuments((prevDocs: any[]) => {
            let changed = false;
            const nextDocs = prevDocs.map(doc => {
              const ds = statusData.documents.find((d: any) => d.id === doc.id);
              if (ds && (doc.status !== ds.status || doc.progress !== ds.progress)) {
                changed = true;
                return { ...doc, status: ds.status, progress: ds.progress };
              }
              return doc;
            });
            return changed ? nextDocs : prevDocs;
          });

          // If everything just finished, fetch full data once to see new biomarkers/results
          const stillExamProcessing = statusData.extraction_status && 
            !['completed', 'failed'].includes(statusData.extraction_status);
          const stillAnyDocProcessing = statusData.documents.some((d: any) => 
            ['processing', 'uploaded'].includes(d.status)
          );

          if (!stillExamProcessing && !stillAnyDocProcessing) {
            // Processing finished!
            fetchData(false);
          }
        } catch (err) {
          console.error("Polling failed", err);
        }
        
        // Detect stall (5 minutes)
        if (pollStartTime && (Date.now() - pollStartTime > 300000)) {
          setIsStalled(true);
        }
      }, 3000);
    } else {
      setPollStartTime(null);
      setIsStalled(false);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [
    examination?.extraction_status, 
    // We need to re-run this effect if ANY document changes status to/from processing
    documents.some(d => ['processing', 'uploaded'].includes(d.status)),
    examinationId, 
    pollStartTime
  ]);

  useEffect(() => {
    const loadMetadataOptions = async () => {
      try {
        const [docs, orgs, cats] = await Promise.all([
          listDoctors(),
          listOrganizations(),
          getExaminationCategories()
        ]);
        setAvailableDoctors(docs);
        setAvailableOrganizations(orgs);
        setDynamicCategories(cats);
      } catch (err) {
        console.error("Failed to load staff/orgs/categories", err);
      }
    };
    loadMetadataOptions();
  }, []);


  const handleSaveAll = async () => {
    try {
      const updated = await updateExamination(examinationId!, { 
        notes: notesContent,
        patient_notes: patientNotesContent,
        category: tempCategory,
        examination_date: tempDate,
        doctor_ids: selectedDoctorIds,
        organization_id: selectedOrganizationId
      });
      setExamination(updated);
      setIsGlobalEditing(false);
      setIsEditingNotes(false);
      setIsEditingPatientNotes(false);
      
      // Clear drafts
      await offlineService.deleteDraft(`${examinationId}-notes`);
      await offlineService.deleteDraft(`${examinationId}-patient-notes`);
      setHasNotesDraft(false);
      setHasPatientNotesDraft(false);
      
      // Force a full data refresh to ensure all relationships and UI elements are in sync
      fetchData(false);
    } catch (error) {
      console.error("Failed to save all changes", error);
    }
  };

  const handleCancelAll = () => {
    setIsGlobalEditing(false);
    setIsEditingNotes(false);
    setIsEditingPatientNotes(false);
    
    // Reset contents from examination object
    if (examination) {
      setNotesContent(examination.notes || '');
      setPatientNotesContent(examination.patient_notes || '');
      setTempCategory(examination.category || '');
      setTempDate(examination.examination_date?.split('T')[0] || '');
      setSelectedDoctorIds(examination.doctors?.map((d: any) => d.id) || []);
      setSelectedOrganizationId(examination.organization_id || null);
    }
  };

  const fetchData = async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const [docsData, examData] = await Promise.all([
        getExaminationDocuments(examinationId!),
        getExamination(examinationId!)
      ]);
      setDocuments(docsData);
      setExamination(examData);
      
      // Check if data is from offline cache (assuming updatedAt exists only in cached objects)
      setIsFromCache(!!examData.updatedAt && !navigator.onLine);

      if (examData.doctors) {
        setSelectedDoctorIds(examData.doctors.map((d: any) => d.id));
      } else {
        setSelectedDoctorIds([]);
      }
      
      setSelectedOrganizationId(examData.organization_id || null);
      
      if (examData.notes && !isEditingNotes) {
        setNotesContent(examData.notes);
      }
      if (examData.patient_notes && !isEditingPatientNotes) {
        setPatientNotesContent(examData.patient_notes);
      }
    } catch (error) {
      console.error('Failed to fetch data for examination:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveNotes = async () => {
    try {
      const updated = await updateExamination(examinationId!, { notes: notesContent });
      setIsEditingNotes(false);
      setExamination(updated);
      await offlineService.deleteDraft(`${examinationId}-notes`);
      setHasNotesDraft(false);
    } catch (error) {
      console.error("Failed to save notes", error);
    }
  };

  const handleSavePatientNotes = async () => {
    try {
      const updated = await updateExamination(examinationId!, { patient_notes: patientNotesContent });
      setIsEditingPatientNotes(false);
      setExamination(updated);
      await offlineService.deleteDraft(`${examinationId}-patient-notes`);
      setHasPatientNotesDraft(false);
    } catch (error) {
      console.error("Failed to save patient notes", error);
    }
  };

  const handleDeleteBiomarker = (observationId: string) => {
    showConfirmation({
      title: t('biomarkers.delete_title'),
      message: t('biomarkers.delete_message'),
      confirmLabel: t('common.delete'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteObservation(observationId);
          fetchData(false);
        } catch (error) {
          console.error("Failed to delete biomarker", error);
        }
      }
    });
  };

  const handleCreateDoctor = async (name: string) => {
    try {
      const newDoc = await createDoctor({ name });
      setAvailableDoctors(prev => [...prev, newDoc]);
      setSelectedDoctorIds(prev => [...new Set([...prev, newDoc.id])]);
    } catch (err) {
      console.error("Failed to create doctor", err);
      alert(t('examination_detail.header.failed_add_doctor'));
    }
  };

  const handleCreateOrganization = async (name: string) => {
    try {
      const newOrg = await createOrganization({ name, active: true });
      setAvailableOrganizations(prev => [...prev, newOrg]);
      setSelectedOrganizationId(newOrg.id);
    } catch (err) {
      console.error("Failed to create organization", err);
      alert(t('organizations.failed_save'));
    }
  };

  const handleCreateCategory = async (name: string) => {
    try {
      const newCat = await createExaminationCategory({ name, slug: name.toLowerCase().replace(/\s+/g, '-') });
      setDynamicCategories(prev => [...prev, newCat]);
      setTempCategory(newCat.name);
    } catch (err) {
      console.error("Failed to create category", err);
      alert("Failed to create category");
    }
  };

  const handleDeleteExamination = () => {

    showConfirmation({
      title: t('examination_detail.header.delete_title'),
      message: t('examination_detail.header.delete_message'),
      confirmLabel: t('examination_detail.header.delete_confirm'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await deleteExamination(examinationId!);
          navigate('/examinations');
        } catch (error) {
          console.error("Failed to delete examination", error);
        }
      }
    });
  };

  const handleToggleInclusion = async (docId: string, currentInclude: boolean) => {
    try {
      // Optimistic update to UI for immediate feedback
      setDocuments(prev => prev.map(d => 
        d.id === docId ? { 
          ...d, 
          include_in_extraction: !currentInclude,
          // If we're including it and it hasn't been processed, show as processing
          status: (!currentInclude && (d.status === 'uploaded' || d.status === 'failed')) ? 'processing' : d.status,
          progress: (!currentInclude && (d.status === 'uploaded' || d.status === 'failed')) ? 10 : d.progress
        } : d
      ));

      // Also set examination to processing state as toggling inclusion triggers cumulative extraction
      setExamination((prev: any) => ({
        ...prev,
        extraction_status: 'processing',
        extraction_progress: Math.max(prev?.extraction_progress || 0, 10)
      }));

      await updateDocument(docId, { include_in_extraction: !currentInclude });
      
      // Refresh data to ensure we have the correct state from server
      await fetchData(false);
    } catch (error) {
      console.error("Failed to toggle inclusion", error);
      // Revert on error if needed, but fetchData will likely fix it
      fetchData(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(e.target.files)) {
        await uploadDocument(file, examination?.patient_id, examinationId);
      }
      await fetchData(false);
      setActiveMainTab('documents');
    } catch (error) {
      console.error("Upload failed", error);
    } finally {
      setUploading(false);
    }
  };

  const handleRunAnalysis = async (mode: 'full' | 'extract_only' = 'full') => {
    if (!examinationId) return;
    setIsAnalysisDropdownOpen(false);
    try {
      // Optimistic update
      setExamination((prev: any) => ({
        ...prev,
        extraction_status: 'processing',
        extraction_progress: 10
      }));
      
      await extractExamination(examinationId, mode);
      fetchData(false);
    } catch (error) {
      console.error("Manual extraction failed", error);
      fetchData(false);
    }
  };


  const openViewer = (doc: any) => {
    if (doc.filename.toLowerCase().endsWith('.dcm')) {
      setDicomViewerDoc(doc);
    } else if (doc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i)) {
      setViewerDoc(doc);
    } else if (doc.filename.match(/\.pdf$/i)) {
      setPdfViewerDoc(doc);
    } else {
      setTextViewerDoc(doc);
    }
  };

  const handleDownload = async (e: React.MouseEvent, id: string, filename: string) => {
    e.stopPropagation();
    try {
      const url = await getDocumentDownloadUrl(id);
      const res = await fetch(url);
      const blob = await res.blob();
      const bUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = bUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(bUrl);
    } catch (err) {
      console.error(err);
    }
  };

  if (loading) {
    return <LoadingState variant="section" showText={true} message={t('examination_detail.loading')} />;
  }

  return (
    <div className="max-w-7xl mx-auto pb-20">
      {/* Offline Data Banner */}
      {isFromCache && (
        <div className="mb-6 flex items-center justify-between px-6 py-3 bg-amber-50 border border-amber-200 rounded-2xl animate-in slide-in-from-top-4 duration-500">
           <div className="flex items-center gap-3">
              <div className="p-2 bg-amber-100 rounded-xl text-amber-600">
                 <CloudLightning className="w-5 h-5" />
              </div>
              <div>
                 <p className="text-sm font-bold text-amber-900">{t('examination_detail.offline_banner.title')}</p>
                 <p className="text-xs text-amber-700">{t('examination_detail.offline_banner.subtitle')}</p>
              </div>
           </div>
        </div>
      )}

      <PageHeader
        title={examination?.category || t('examination_detail.header.general_examination')}
        subtitle={
          <div className="flex flex-col space-y-1">
            <span className="font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest text-xs">
              {new Date(examination?.examination_date).toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
            </span>
            <div className="flex items-center space-x-2">
              <div className="flex -space-x-1.5 mr-1">
                {examination?.doctors?.map((d: any) => (
                  <div key={d.id} className="w-6 h-6 rounded-full border border-white dark:border-dark-bg bg-blue-100 flex items-center justify-center text-[8px] font-black text-blue-600 uppercase" title={`${t('doctors.dr')} ${d.name}`}>
                    {d.name.substring(0, 2)}
                  </div>
                ))}
              </div>
              <p className="text-xs font-medium text-gray-500 dark:text-dark-muted">
                {examination?.doctors?.length > 0 ? `${t('doctors.dr')} ${examination.doctors.map((d: any) => d.name).join(', ')}` : t('examinations.no_doctor_assigned')}
              </p>
            </div>
            {examination?.organization && (
              <div className="flex items-center space-x-1.5 text-blue-600 dark:text-blue-400">
                <Building2 className="w-3.5 h-3.5" />
                <span className="text-[10px] font-black uppercase tracking-widest">{examination.organization.name}</span>
              </div>
            )}
          </div>
        }
        icon={examination?.category_details?.icon ? <DynamicIcon icon={examination.category_details.icon} /> : <Stethoscope />}
        breadcrumbs={[
          { label: t('examinations.title'), path: '/examinations' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex flex-col md:flex-row items-end md:items-center justify-between gap-4 w-full">
            <div className="flex flex-wrap items-center gap-3">
              {!isGlobalEditing && (
                <>
                  <input type="file" multiple ref={fileInputRef} onChange={handleFileUpload} className="hidden" accept=".pdf,.jpg,.jpeg,.png,.docx,.txt,.dcm" id="add-doc-input" />
                  <input type="file" ref={cameraInputRef} onChange={handleFileUpload} className="hidden" accept="image/*" capture="environment" id="camera-doc-input" />
                  
                  <div className="flex items-center gap-2">
                    <button onClick={() => fileInputRef.current?.click()} disabled={uploading} className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95 disabled:opacity-50">
                      <Plus className="w-5 h-5" />
                      <span>{uploading ? t('examination_detail.header.processing') : t('examination_detail.header.upload_results')}</span>
                    </button>
                    {isMobileDevice() && (
                      <button onClick={() => cameraInputRef.current?.click()} disabled={uploading} className="flex items-center space-x-2 px-4 py-2.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-900/30 rounded-xl font-bold text-sm hover:bg-indigo-100 transition-all active:scale-95 disabled:opacity-50" title="Take Photo">
                        <Camera className="w-5 h-5" />
                      </button>
                    )}
                  </div>
                  <div className="relative" ref={analysisDropdownRef}>
                    <button 
                      onClick={() => setIsAnalysisDropdownOpen(!isAnalysisDropdownOpen)} 
                      disabled={uploading || (examination?.extraction_status && !['completed', 'failed'].includes(examination.extraction_status))} 
                      className="flex items-center space-x-2 px-6 py-2.5 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-900/30 rounded-xl font-bold text-sm hover:bg-indigo-100 dark:hover:bg-indigo-900/30 transition-all active:scale-95 disabled:opacity-50"
                    >
                      <Activity className="w-5 h-5" />
                      <span>{t('examination_detail.header.run_ai_analysis')}</span>
                      <ChevronDown className={`w-4 h-4 transition-transform ${isAnalysisDropdownOpen ? 'rotate-180' : ''}`} />
                    </button>
                    {isAnalysisDropdownOpen && (
                      <div className="absolute right-0 mt-2 w-72 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-2xl z-[110] overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                        <div className="p-4 border-b border-gray-50 dark:border-dark-border">
                          <p className="text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.header.select_extraction_mode')}</p>
                        </div>
                        <div className="p-2">
                          <button onClick={() => handleRunAnalysis('full')} className="w-full flex items-center space-x-3 p-3 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-xl transition-colors text-left group">
                            <div className="p-2 bg-indigo-100 dark:bg-indigo-900/40 rounded-lg text-indigo-600 group-hover:bg-indigo-600 group-hover:text-white transition-colors"><Cpu className="w-4 h-4" /></div>
                            <div>
                              <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase flex items-center gap-2">
                                {t('examination_detail.header.full_reconstruction')}
                                <AIBadge workflow="full_reconstruction" className="ml-1" />
                              </p>
                              <p className="text-[10px] text-gray-400 font-medium">{t('examination_detail.header.full_reconstruction_desc')}</p>
                            </div>
                          </button>
                          <button onClick={() => handleRunAnalysis('extract_only')} className="w-full flex items-center space-x-3 p-3 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-colors text-left group">
                            <div className="p-2 bg-blue-100 dark:bg-blue-900/40 rounded-lg text-blue-600 group-hover:bg-blue-600 group-hover:text-white transition-colors"><FlaskConical className="w-4 h-4" /></div>
                            <div>
                              <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase flex items-center gap-2">
                                {t('examination_detail.header.fast_extraction')}
                                <AIBadge workflow="fast_extraction" className="ml-1" />
                              </p>
                              <p className="text-[10px] text-gray-400 font-medium">{t('examination_detail.header.fast_extraction_desc')}</p>
                            </div>
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                  <ExaminationAIActions examinationId={examinationId!} />
                </>
              )}
            </div>

            <div className="flex items-center gap-3">
              {!isGlobalEditing ? (
                <div className="flex items-center space-x-2">
                  <button onClick={handleDeleteExamination} className="p-2 text-gray-400 hover:text-red-600 transition-all active:scale-95" title={t('examination_detail.header.discard_record')}>
                    <Trash2 className="w-5 h-5" />
                  </button>
                  <button 
                    onClick={() => {
                      setTempDate(examination?.examination_date?.split('T')[0] || '');
                      setTempCategory(examination?.category || '');
                      setNotesContent(examination?.notes || '');
                      setPatientNotesContent(examination?.patient_notes || '');
                      setSelectedDoctorIds(examination?.doctors?.map((d: any) => d.id) || []);
                      setSelectedOrganizationId(examination?.organization_id || null);
                      setIsGlobalEditing(true);
                      setIsEditingNotes(true);
                      setIsEditingPatientNotes(true);
                    }} 
                    className="flex items-center space-x-2 px-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-[#1a2b4b] dark:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all font-semibold shadow-sm text-sm whitespace-nowrap"
                  >
                    <Edit2 className="w-4 h-4" />
                    <span>{t('common.edit')}</span>
                  </button>
                </div>
              ) : (
                <div className="flex items-center space-x-2">
                  <button onClick={handleCancelAll} className="flex items-center justify-center space-x-2 px-4 py-2 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl hover:bg-gray-50 dark:hover:bg-dark-border transition-all font-semibold text-sm">
                    <X className="w-4 h-4" />
                    <span>{t('common.cancel')}</span>
                  </button>
                  <button onClick={handleSaveAll} className="flex items-center justify-center space-x-2 px-6 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-all shadow-lg shadow-emerald-200/50 dark:shadow-none font-bold text-sm active:scale-95">
                    <Check className="w-4 h-4" />
                    <span>{t('common.save')}</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        }
      />


        {/* Task Progress Indicator - Reusable component for all statuses */}
        <div className="mb-8">
          <TaskProgressIndicator 
            examinationId={examinationId}
            examinationStatus={examination?.extraction_status}
            examinationProgress={examination?.extraction_progress}
            errorMessage={examination?.error_message}
            documents={documents}
          />
          
          {/* Show Retry Button if failed (distinct from progress component for better UX) */}
          {examination?.extraction_status === 'failed' && (
            <div className="mt-4 flex justify-end">
              <button 
                onClick={() => handleRunAnalysis('full')}
                className="flex items-center space-x-2 px-8 py-3 bg-red-600 text-white rounded-xl font-bold text-sm uppercase hover:bg-red-700 transition-all active:scale-95 shadow-lg shadow-red-200/50 dark:shadow-none"
              >
                <RotateCcw className="w-4 h-4" />
                <span>{t('examination_detail.header.retry_full_analysis')}</span>
              </button>
            </div>
          )}
        </div>
        
        {/* Associated Clinical Events */}
        <div className="mb-12">
          <AssociatedEvents 
            examinationId={examinationId!} 
            patientId={examination?.patient_id} 
            isEditing={isGlobalEditing} 
          />
        </div>

       {/* 2. SUMMARY HIGHLIGHTS GRID */}
 
       <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 mb-12">
            {/* Examination Info Card */}
            <div className="xl:col-span-1 bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm flex flex-col">
               <div className="flex items-center space-x-3 mb-8">
                  <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-xl">
                     <ClipboardList className="w-5 h-5 text-blue-500" />
                  </div>
                  <h3 className="text-lg font-black text-[#1a2b4b] dark:text-dark-text tracking-tight uppercase">{t('common.info')}</h3>
               </div>
 
               <div className="space-y-6">
                  <div className="flex items-start space-x-4">
                     <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                        <Calendar className="w-4 h-4 text-gray-400" />
                     </div>
                     <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{t('common.date')}</p>
                        {isGlobalEditing ? (
                           <input 
                             type="date" 
                             className="w-full py-2 bg-transparent text-sm font-bold outline-none dark:text-dark-text"
                             value={tempDate}
                             onChange={(e) => setTempDate(e.target.value)}
                           />
                        ) : (
                           <p className="font-bold text-[#1a2b4b] dark:text-dark-text">
                              {new Date(examination?.examination_date).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}
                           </p>
                        )}
                     </div>
                  </div>
 
                  <div className="flex items-start space-x-4">
                     <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                        <Building2 className="w-4 h-4 text-gray-400" />
                     </div>
                     <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{t('organizations.hospital')}</p>
                        {isGlobalEditing ? (
                           <OrganizationSelector
                             organizations={availableOrganizations}
                             selectedId={selectedOrganizationId}
                             onSelect={(id) => setSelectedOrganizationId(id)}
                             onCreate={handleCreateOrganization}
                             placeholder={t('organizations.title')}
                             className="border-none"
                           />
                        ) : (
                           examination?.organization ? (
                              <Link to={`/organizations/${examination.organization.id}`} className="font-bold text-blue-600 hover:underline">
                                 {examination.organization.name}
                              </Link>
                           ) : (
                              <p className="font-bold text-gray-400 italic text-sm">{t('common.unknown')}</p>
                           )
                        )}
                     </div>
                  </div>
 
                  <div className="flex items-start space-x-4">
                     <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                        <Stethoscope className="w-4 h-4 text-gray-400" />
                     </div>
                     <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-2">{t('common.doctors')}</p>
                        {isGlobalEditing ? (
                           <DoctorSelector
                             doctors={availableDoctors}
                             selectedIds={selectedDoctorIds}
                             onSelect={(id) => setSelectedDoctorIds(prev => [...new Set([...prev, id])])}
                             onDeselect={(id) => setSelectedDoctorIds(prev => prev.filter(i => i !== id))}
                             onCreateDoctor={handleCreateDoctor}
                             placeholder={t('examinations.attending_physician')}
                             className="border-none"
                           />
                        ) : (
                           <div className="space-y-2">
                              {examination?.doctors?.length > 0 ? (
                                 examination.doctors.map((d: any) => (
                                    <Link key={d.id} to={`/doctors/${d.id}`} className="flex items-center space-x-2 group">
                                       <div className="w-6 h-6 rounded-full bg-blue-100 flex items-center justify-center text-[8px] font-black text-blue-600 shadow-sm group-hover:bg-blue-600 group-hover:text-white transition-all">
                                          {d.name.substring(0, 2).toUpperCase()}
                                       </div>
                                       <span className="text-sm font-bold text-gray-700 dark:text-dark-text group-hover:text-blue-600 transition-colors">{t('doctors.dr')} {d.name}</span>
                                    </Link>
                                 ))
                              ) : (
                                 <p className="font-bold text-gray-400 italic text-sm">{t('examinations.no_doctor_assigned')}</p>
                              )}
                           </div>
                        )}
                     </div>
                  </div>
 
                  <div className="flex items-start space-x-4">
                     <div className="p-2 bg-gray-50 dark:bg-dark-bg rounded-lg shrink-0">
                        <Bookmark className="w-4 h-4 text-gray-400" />
                     </div>
                     <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-black uppercase tracking-widest text-gray-400 mb-1">{t('common.category')}</p>
                        {isGlobalEditing ? (
                           <CategorySelector
                             categories={dynamicCategories}
                             selectedName={tempCategory}
                             onSelect={(name) => setTempCategory(name)}
                             onCreate={handleCreateCategory}
                             placeholder={t('examination_detail.header.select_category')}
                             className="border-none"
                           />
                        ) : (
                           <span className="px-2 py-0.5 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 text-[10px] font-black uppercase tracking-widest rounded-lg border border-indigo-100 dark:border-indigo-800/50">
                              {examination?.category || t('examination_detail.header.general_examination')}
                           </span>
                        )}
                     </div>
                  </div>
               </div>
            </div>

            {/* Key Biomarkers Widget */}
            <div className="xl:col-span-2 bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm flex flex-col">
               <div className="flex items-center justify-between mb-8">
                  <div className="flex items-center space-x-3">
                     <div className="p-2 bg-red-50 dark:bg-red-900/20 rounded-xl">
                        <Activity className="w-5 h-5 text-red-500" />
                     </div>
                     <h3 className="text-lg font-black text-[#1a2b4b] dark:text-dark-text tracking-tight uppercase">{t('examination_detail.widgets.critical_biomarkers')}</h3>
                  </div>
                  
                  <div className="flex items-center space-x-6">
                    <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                        <button 
                          onClick={() => setBiomarkerDataMode('normalized')}
                          className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${biomarkerDataMode === 'normalized' ? 'bg-white dark:bg-dark-surface text-indigo-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                        >
                          {t('biomarkers.data_modes.normalized')}
                        </button>
                        <button 
                          onClick={() => setBiomarkerDataMode('raw')}
                          className={`px-4 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${biomarkerDataMode === 'raw' ? 'bg-white dark:bg-dark-surface text-amber-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                        >
                          {t('biomarkers.data_modes.raw')}
                        </button>
                    </div>
                    <button onClick={() => handleTabChange('biomarkers')} className="text-xs font-bold text-blue-600 uppercase hover:underline">{t('examination_detail.widgets.full_report')} &rarr;</button>
                  </div>
               </div>
               
               <BiomarkerList
                 biomarkers={allExamBiomarkers}
                 groupedData={[['', augmentedKeyBiomarkers]]}
                 viewMode="grid"
                 compact={true}
                 showCharts={false}
                 perspective="clinical"
                 showDate={false}
                 showSource={false}
                 showReferenceRanges={showReferenceRanges}
                 dataMode={biomarkerDataMode}
                 onDataModeChange={setBiomarkerDataMode}
                 hideDataModeToggle={true}
                 initialDataMode="raw"
                 emptyMessage={t('examination_detail.widgets.no_extracted_data')}
               />
            </div>
       </div>


      {/* 3. MAIN TABBED CONTENT AREA */}
      <div ref={tabsRef} className="bg-white dark:bg-dark-surface rounded-[2.5rem] border border-gray-100 dark:border-dark-border shadow-2xl shadow-blue-900/5 overflow-hidden scroll-mt-32">
         <div className="px-8 border-b border-gray-50 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/20">
          <nav className="flex items-center space-x-10 overflow-x-auto no-scrollbar">
             {[
               { id: 'overview', label: t('examination_detail.tabs.overview'), icon: ClipboardList },
               { id: 'biomarkers', label: t('examination_detail.tabs.biomarkers'), icon: FlaskConical },
               { id: 'imaging', label: t('examination_detail.tabs.imaging'), icon: ImageIcon },
               { id: 'documents', label: t('examination_detail.tabs.repository'), icon: FileText }
             ].map(tab => (
               <button 
                 key={tab.id}
                 onClick={() => handleTabChange(tab.id as any)}
                 className={`flex items-center space-x-2 py-6 border-b-4 transition-all whitespace-nowrap ${
                   activeMainTab === tab.id 
                     ? 'border-blue-600 text-blue-600 dark:text-blue-400' 
                     : 'border-transparent text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
                 }`}
               >

                   <tab.icon className={`w-4 h-4 ${activeMainTab === tab.id ? 'text-blue-600' : 'text-gray-300'}`} />
                   <span className="text-xs font-black uppercase tracking-widest">{tab.label}</span>
                 </button>
               ))}
            </nav>
         </div>

         <div className="p-8 lg:p-12">
            {/* OVERVIEW TAB */}
            {activeMainTab === 'overview' && (
              <div className="space-y-12 animate-in fade-in duration-500">
                {/* Notes Row: Physician and Patient Notes side-by-side on lg+ */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
                   {/* Doctor's Notes */}
                    <div className="space-y-6">
                       <div className="flex items-center justify-between">
                           <div className="flex items-center space-x-3">
                              <h3 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.3em]">{t('examination_detail.overview.physician_notes')}</h3>
                              {hasNotesDraft && !isGlobalEditing && (
                                 <button 
                                    onClick={applyNotesDraft} 
                                    className="flex items-center gap-1 px-2 py-0.5 bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 rounded-full text-[10px] font-bold border border-amber-200 animate-pulse hover:animate-none"
                                 >
                                    <RotateCcw className="w-2.5 h-2.5" />
                                    <span>{t('examination_detail.overview.restore_draft')}</span>
                                 </button>
                              )}
                           </div>
                       </div>
 
                        {isEditingNotes ? (
                            <div className="space-y-4">
                               <RichTextEditor value={notesContent} onChange={setNotesContent} placeholder={t('examination_detail.overview.physician_notes')} />
                            </div>
                        ) : (
                           <div className={`prose dark:prose-invert max-w-none bg-gray-50/50 dark:bg-dark-bg/30 p-10 rounded-[2.5rem] ${examination?.notes ? 'min-h-[350px]' : 'min-h-[100px] flex items-center justify-center'} border border-gray-100 dark:border-dark-border shadow-inner`}>
                              {examination?.notes ? <div dangerouslySetInnerHTML={{ __html: examination.notes }} /> : <p className="text-gray-400 italic">{t('examination_detail.overview.no_notes')}</p>}
                           </div>
                        )}
                    </div>
 
                    {/* Patient's Notes */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center space-x-3">
                               <h3 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.3em]">{t('examination_detail.overview.patient_notes_title')}</h3>
                               {hasPatientNotesDraft && !isGlobalEditing && (
                                  <button 
                                     onClick={applyPatientNotesDraft} 
                                     className="flex items-center gap-1 px-2 py-0.5 bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400 rounded-full text-[10px] font-bold border border-indigo-200 animate-pulse hover:animate-none"
                                  >
                                     <RotateCcw className="w-2.5 h-2.5" />
                                     <span>{t('examination_detail.overview.restore_draft')}</span>
                                  </button>
                               )}
                            </div>
                        </div>
 
                        {isEditingPatientNotes ? (
                           <div className="space-y-4">
                              <RichTextEditor value={patientNotesContent} onChange={setPatientNotesContent} placeholder={t('examination_detail.overview.patient_notes_placeholder')} />
                           </div>
                        ) : (
                          <div className={`prose dark:prose-invert max-w-none bg-blue-50/20 dark:bg-blue-900/5 p-10 rounded-[2.5rem] ${examination?.patient_notes ? 'min-h-[350px]' : 'min-h-[100px] flex items-center justify-center'} border border-blue-100/50 dark:border-blue-900/10 shadow-inner`}>
                             {examination?.patient_notes ? <div dangerouslySetInnerHTML={{ __html: examination.patient_notes }} /> : <p className="text-gray-400 italic text-sm">{t('examination_detail.overview.patient_notes_empty')}</p>}
                          </div>
                       )}
                    </div>
                </div>

                {/* Additional Clinical Data Section (Impressions, Diagnoses, Meds) */}
                {!isGlobalEditing && (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
                    <div className="space-y-8">
                       {examination?.impressions && (
                         <div className="bg-white dark:bg-dark-surface/40 p-8 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm animate-in slide-in-from-bottom-4 duration-500">
                           <div className="flex items-center justify-between mb-6">
                             <div className="flex items-center space-x-2">
                               <BriefcaseMedical className="w-4 h-4 text-blue-500" />
                               <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('examination_detail.overview.clinical_impression')}</p>
                             </div>
                             <AIBadge />
                           </div>
                           <div className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-dark-text leading-relaxed">
                             <ReactMarkdown>{examination.impressions}</ReactMarkdown>
                           </div>
                         </div>
                       )}

                       {examination?.diagnoses?.length > 0 && (
                         <div className="bg-white dark:bg-dark-surface/40 p-8 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm animate-in slide-in-from-bottom-4 duration-500">
                           <div className="flex items-center justify-between mb-6">
                             <div className="flex items-center space-x-2">
                               <Bookmark className="w-4 h-4 text-blue-500" />
                               <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('examination_detail.overview.extracted_diagnoses')}</p>
                             </div>
                             <AIBadge />
                           </div>
                           <div className="flex flex-wrap gap-2">
                             {examination.diagnoses.map((d: string) => (
                               <span key={d} className="px-4 py-2 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded-xl text-xs font-bold border border-blue-100 dark:border-blue-800/30 shadow-sm">{d}</span>
                             ))}
                           </div>
                         </div>
                       )}
                    </div>

                    <div className="space-y-8">
                       {examination?.medications?.length > 0 && (
                          <div className="bg-white dark:bg-dark-surface/40 p-8 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm animate-in slide-in-from-bottom-4 duration-500 h-full">
                            <div className="flex items-center justify-between mb-6">
                              <div className="flex items-center space-x-2">
                                <Pill className="w-4 h-4 text-indigo-500" />
                                <p className="text-xs font-black text-gray-900 dark:text-dark-text uppercase tracking-widest">{t('examination_detail.overview.identified_medications')}</p>
                              </div>
                              <AIBadge />
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                               {examination.medications.map((m: any, idx: number) => (
                                 <MedicationCard 
                                   key={idx} 
                                   medication={{
                                     ...m,
                                     status: m.status || 'active',
                                     id: m.id || `ext-${idx}`
                                   } as any}
                                   showActions={false}
                                 />
                               ))}
                            </div>
                          </div>
                       )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* BIOMARKERS TAB - Categorized List */}
            {activeMainTab === 'biomarkers' && (
               <div className="space-y-12 animate-in slide-in-from-bottom-4 duration-500">
                  <div className="flex items-center justify-between mb-8">
                     <div className="flex items-center space-x-1 bg-gray-100 dark:bg-dark-bg p-1 rounded-xl">
                        <button 
                           onClick={() => setBiomarkerPerspective('clinical')}
                           className={`px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition-all ${biomarkerPerspective === 'clinical' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400'}`}
                        >
                           {t('examination_detail.biomarkers.clinical')}
                        </button>
                        <button 
                           onClick={() => setBiomarkerPerspective('technical')}
                           className={`px-4 py-1.5 rounded-lg text-xs font-black uppercase tracking-widest transition-all ${biomarkerPerspective === 'technical' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400'}`}
                        >
                           {t('examination_detail.biomarkers.technical')}
                        </button>
                     </div>

                     <div className="flex items-center bg-gray-100 dark:bg-dark-bg p-1 rounded-xl border border-gray-200 dark:border-dark-border">
                        <button 
                           onClick={() => setBiomarkerViewMode('table')} 
                           className={`p-1.5 rounded-lg transition-all ${biomarkerViewMode === 'table' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                           title={t('biomarkers.views.table')}
                        >
                           <TableIcon className="w-4 h-4" />
                        </button>
                        <button 
                           onClick={() => setBiomarkerViewMode('list')} 
                           className={`p-1.5 rounded-lg transition-all ${biomarkerViewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                           title={t('biomarkers.views.list')}
                        >
                           <List className="w-4 h-4" />
                        </button>
                        <button 
                           onClick={() => setBiomarkerViewMode('grid')} 
                           className={`p-1.5 rounded-lg transition-all ${biomarkerViewMode === 'grid' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                           title={t('biomarkers.views.grid')}
                        >
                           <LayoutGrid className="w-4 h-4" />
                        </button>
                     </div>

                     <div className="flex items-center space-x-4">
                        <button 
                           onClick={() => setIsAddBiomarkerOpen(true)}
                           className="flex items-center space-x-2 px-4 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 transition-all text-[10px] font-black uppercase tracking-widest border border-blue-100 dark:border-blue-800/30"
                        >
                           <Plus className="w-3.5 h-3.5" />
                           <span>{t('examination_detail.biomarkers.add_result')}</span>
                        </button>
                        <div className="flex items-center space-x-2 text-xs font-bold text-gray-400">
                           <FlaskConical className="w-4 h-4" />
                           <span>{t('examination_detail.biomarkers.measured_parameters', { count: allExamBiomarkers.length })}</span>
                        </div>
                     </div>
                  </div>

                  <BiomarkerList
                    biomarkers={allExamBiomarkers}
                    groupedData={biomarkerGroups}
                    viewMode={biomarkerViewMode}
                    showCharts={false}
                    perspective={biomarkerPerspective}
                    showDate={false}
                    showSource={false}
                    showReferenceRanges={showReferenceRanges}
                    dataMode={biomarkerDataMode}
                    onDataModeChange={setBiomarkerDataMode}
                    initialDataMode="raw"
                    emptyMessage={t('examination_detail.biomarkers.no_data_title')}
                    emptySubtitle={t('examination_detail.biomarkers.no_data_subtitle')}
                    onDelete={isGlobalEditing ? handleDeleteBiomarker : undefined}
                  />
               </div>
            )}

            {/* IMAGING TAB - Gallery View */}
            {activeMainTab === 'imaging' && (
              <div className="animate-in zoom-in-95 duration-500">
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-8">
                  {imagingDocs.map(doc => (
                    <div 
                      key={doc.id}
                      onClick={() => openViewer(doc)}
                      className="group bg-white dark:bg-dark-surface rounded-[2rem] border border-gray-100 dark:border-dark-border overflow-hidden shadow-sm hover:shadow-2xl transition-all cursor-pointer aspect-square flex flex-col"
                    >
                      <div className="flex-1 bg-gray-950 flex items-center justify-center overflow-hidden relative">
                         <AuthenticatedThumbnail documentId={doc.id} filename={doc.filename} className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-1000 ease-out opacity-90 group-hover:opacity-100" />
                         <div className="absolute inset-0 bg-blue-600/0 group-hover:bg-blue-600/10 transition-colors" />
                         <div className="absolute bottom-4 right-4 p-2 bg-black/60 backdrop-blur-md rounded-xl opacity-0 group-hover:opacity-100 transition-opacity">
                            <ImageIcon className="w-4 h-4 text-white" />
                         </div>
                      </div>
                      <div className="p-5 border-t border-gray-50 dark:border-dark-border">
                         <p className="text-xs font-black text-gray-900 dark:text-dark-text truncate mb-1">{doc.filename}</p>
                         <div className="flex items-center justify-between">
                            <span className="text-[9px] font-black text-blue-600 dark:text-blue-400 uppercase tracking-widest">{doc.entities?.document_category || t('examination_detail.imaging.clinical_scan')}</span>
                            <Clock className="w-3 h-3 text-gray-300" />
                         </div>
                      </div>
                    </div>
                  ))}

                  {imagingDocs.length === 0 && (
                    <div className="col-span-full py-32 text-center bg-gray-50/30 dark:bg-dark-bg/20 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-dark-border">
                      <ImageIcon className="w-16 h-16 text-gray-200 mx-auto mb-6" />
                      <h4 className="text-lg font-bold text-gray-500">{t('examination_detail.imaging.no_visual_diagnostics')}</h4>
                      <p className="text-gray-400 text-sm mt-2">{t('examination_detail.imaging.no_visual_diagnostics_subtitle')}</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* REPOSITORY TAB - Documents Grid/List */}
            {activeMainTab === 'documents' && (
              <div className="space-y-8 animate-in fade-in duration-500 w-full overflow-hidden">
                <div className="flex flex-col sm:flex-row sm:items-center justify-end gap-6">
                   <div className="flex items-center space-x-3">
                      <div className="flex items-center bg-gray-100 dark:bg-dark-bg p-1.5 rounded-2xl border border-gray-200 dark:border-dark-border">
                         <button onClick={() => setDocViewMode('list')} className={`p-2 rounded-xl transition-all ${docViewMode === 'list' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400 hover:text-gray-600'}`}>
                           <List className="w-5 h-5" />
                         </button>
                         <button onClick={() => setDocViewMode('grid')} className={`p-2 rounded-xl transition-all ${docViewMode === 'grid' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-md' : 'text-gray-400 hover:text-gray-600'}`}>
                           <LayoutGrid className="w-5 h-5" />
                         </button>
                      </div>
                      <button onClick={() => fileInputRef.current?.click()} className="p-3 bg-blue-600 text-white rounded-2xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-600/20"><Plus className="w-5 h-5" /></button>
                   </div>
                </div>

                {docViewMode === 'list' ? (
                  <div className="w-full max-w-full">
                    <div className="bg-white dark:bg-dark-surface rounded-[2rem] border border-gray-100 dark:border-dark-border overflow-x-auto shadow-sm w-full" style={{ overflowY: 'visible' }}>
                       <table className="min-w-full divide-y divide-gray-50 dark:divide-dark-border">
                        <thead className="bg-gray-50/50 dark:bg-dark-bg/50">
                          <tr>
                            <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.repository.table.document')}</th>
                            <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.repository.table.included')}</th>
                            <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.repository.table.clinical_context')}</th>
                            <th className="px-8 py-4 text-left text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.repository.table.extraction')}</th>
                            <th className="px-8 py-4 text-right text-[10px] font-black text-gray-400 uppercase tracking-widest">{t('examination_detail.repository.table.actions')}</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                          {documents.filter(d => d.filename.toLowerCase().includes(searchTerm.toLowerCase())).map(doc => (
                            <tr key={doc.id} className="hover:bg-gray-50/50 dark:hover:bg-dark-bg transition-colors group">
                              <td className="px-8 py-5 whitespace-nowrap">
                                 <div className="flex items-center space-x-4 cursor-pointer" onClick={() => openViewer(doc)}>
                                    <div className={`p-2.5 rounded-xl border border-gray-100 dark:border-dark-border ${doc.filename.match(/\.pdf$/i) ? 'bg-red-50 dark:bg-red-900/20' : 'bg-blue-50 dark:bg-blue-900/20'}`}>
                                       {doc.filename.match(/\.pdf$/i) ? <FileText className="w-5 h-5 text-red-600" /> : <ImageIcon className="w-5 h-5 text-blue-600" />}
                                    </div>
                                    <span className="text-sm font-black text-gray-900 dark:text-dark-text group-hover:text-blue-600 transition-colors">{doc.filename}</span>
                                 </div>
                              </td>
                              <td className="px-8 py-5 whitespace-nowrap">
                                 <button
                                    type="button"
                                    onClick={() => handleToggleInclusion(doc.id, doc.include_in_extraction)}
                                    title={doc.include_in_extraction ? "Remove from AI analysis" : "Include in AI extraction & biomarker analysis"}
                                    className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all border ${
                                       doc.include_in_extraction 
                                          ? 'bg-blue-600 border-blue-600 text-white shadow-md shadow-blue-500/20' 
                                          : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-400 hover:text-blue-500 hover:border-blue-200'
                                    }`}
                                 >
                                    <Cpu className="w-3 h-3" />
                                    <span>{doc.include_in_extraction ? t('examination_detail.repository.selected') : t('examination_detail.repository.analyze')}</span>
                                 </button>
                              </td>
                              <td className="px-8 py-5 whitespace-nowrap text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-tighter">
                                 {doc.entities?.document_category || t('examination_detail.repository.general_report')}
                              </td>
                              <td className="px-8 py-5 whitespace-nowrap">
                                <div className="flex flex-col w-32">
                                  <div className="flex items-center space-x-2 mb-1">
                                    <span className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${
                                       doc.status === 'completed' ? 'bg-green-50 text-green-700 border border-green-100' : 
                                       doc.status === 'failed' ? 'bg-red-50 text-red-700 border border-red-100' :
                                       'bg-yellow-50 text-yellow-700 border border-yellow-100 animate-pulse'
                                    }`}>
                                       {doc.status}
                                    </span>
                                    {doc.status === 'processing' && doc.progress !== undefined && (
                                      <span className="text-[9px] font-bold text-yellow-600 uppercase">
                                        {doc.progress}%
                                      </span>
                                    )}
                                  </div>
                                  
                                  {doc.status === 'processing' && (
                                    <div className="w-full h-1 bg-yellow-100 dark:bg-yellow-900/30 rounded-full overflow-hidden">
                                      <div 
                                        className="h-full bg-yellow-500 transition-all duration-500"
                                        style={{ width: `${doc.progress || 0}%` }}
                                      />
                                    </div>
                                  )}

                                  {doc.error_message && (
                                    <div className="mt-1">
                                      <span className={`text-[9px] text-red-500 font-bold leading-snug cursor-pointer whitespace-normal block ${expandedErrorId === doc.id ? '' : 'line-clamp-2 max-w-[150px]'}`}
                                            onClick={(e) => { e.stopPropagation(); setExpandedErrorId(expandedErrorId === doc.id ? null : doc.id); }}>
                                        {doc.error_message}
                                      </span>
                                      {expandedErrorId !== doc.id && doc.error_message.length > 50 && (
                                        <button type="button" onClick={(e) => { e.stopPropagation(); setExpandedErrorId(doc.id); }} className="text-[8px] font-black uppercase text-red-400 hover:text-red-600 mt-0.5">Show more</button>
                                      )}
                                      {expandedErrorId === doc.id && (
                                        <button type="button" onClick={(e) => { e.stopPropagation(); setExpandedErrorId(null); }} className="text-[8px] font-black uppercase text-red-400 hover:text-red-600 mt-0.5">Show less</button>
                                      )}
                                    </div>
                                  )}
                                </div>
                             </td>
                              <td className="px-8 py-5 whitespace-nowrap text-right flex items-center justify-end space-x-2">
                                 <button onClick={(e) => handleDownload(e, doc.id, doc.filename)} className="p-2 hover:bg-gray-100 rounded-xl transition-colors text-gray-400" title={t('common.download')}><Download className="w-4 h-4" /></button>
                                 <Link to={`/documents/${doc.id}`} className="px-4 py-2 bg-gray-50 dark:bg-dark-bg text-[10px] font-black text-gray-500 uppercase rounded-xl hover:bg-gray-900 hover:text-white transition-all">{t('examination_detail.repository.analyze')}</Link>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                     </table>
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-6">
                     {documents.filter(d => d.filename.toLowerCase().includes(searchTerm.toLowerCase())).map(doc => (
                       <div 
                         key={doc.id}
                         onClick={() => openViewer(doc)}
                         className="bg-white dark:bg-dark-surface rounded-3xl border border-gray-100 dark:border-dark-border p-4 shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all cursor-pointer flex flex-col items-center text-center space-y-3 group"
                       >
                          <div className="w-full aspect-[3/4] bg-gray-50 dark:bg-dark-bg rounded-2xl overflow-hidden flex items-center justify-center relative border border-gray-50">
                             <AuthenticatedThumbnail documentId={doc.id} filename={doc.filename} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
                             <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all flex items-center justify-center">
                                <ExternalLink className="w-8 h-8 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
                             </div>
                             {doc.status !== 'completed' && <div className="absolute inset-0 bg-white/60 backdrop-blur-[2px] flex items-center justify-center"><div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin"></div></div>}
                          </div>
                           <div className="w-full px-1">
                              <p className="text-[10px] font-black text-gray-900 dark:text-dark-text truncate leading-tight">{doc.filename}</p>
                              <p className="text-[8px] font-black text-gray-400 uppercase tracking-tighter mt-0.5 mb-3">{doc.entities?.document_category || t('examination_detail.repository.medical_file')}</p>
                              
                              <button
                                 type="button"
                                 onClick={(e) => { e.stopPropagation(); handleToggleInclusion(doc.id, doc.include_in_extraction); }}
                                 className={`w-full flex items-center justify-center space-x-1.5 py-1.5 rounded-xl text-[9px] font-black uppercase tracking-widest transition-all border ${
                                    doc.include_in_extraction 
                                       ? 'bg-blue-600 border-blue-600 text-white shadow-md' 
                                       : 'bg-gray-50 dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400 hover:text-blue-500 hover:border-blue-200'
                                 }`}
                              >
                                 <Cpu className="w-3 h-3" />
                                 <span>{doc.include_in_extraction ? t('examination_detail.repository.selected') : t('examination_detail.repository.analyze')}</span>
                              </button>
                           </div>
                       </div>
                     ))}
                  </div>
                )}
              </div>
            )}
         </div>
      </div>

      {viewerDoc && (

        <AuthenticatedImageViewer 
          documentId={viewerDoc.id} 
          filename={viewerDoc.filename} 
          onClose={() => setViewerDoc(null)} 
          onRefresh={() => fetchData(false)}
        />
      )}
      {dicomViewerDoc && (
        <AuthenticatedDicomViewer
          documentId={dicomViewerDoc.id}
          filename={dicomViewerDoc.filename}
          onClose={() => setDicomViewerDoc(null)}
          onRefresh={() => fetchData(false)}
          gallery={imagingDocs}
        />
      )}
      {pdfViewerDoc && <AuthenticatedPdfViewer documentId={pdfViewerDoc.id} filename={pdfViewerDoc.filename} onClose={() => setPdfViewerDoc(null)} />}
      {textViewerDoc && <AuthenticatedTextViewer documentId={textViewerDoc.id} filename={textViewerDoc.filename} onClose={() => setTextViewerDoc(null)} />}

      <AddBiomarkerModal 
        isOpen={isAddBiomarkerOpen}
        onClose={() => setIsAddBiomarkerOpen(false)}
        patientId={examination?.patient_id}
        examinationId={examinationId!}
        onSuccess={() => fetchData(false)}
      />
    </div>
  );
}

export default ExaminationDetail;
