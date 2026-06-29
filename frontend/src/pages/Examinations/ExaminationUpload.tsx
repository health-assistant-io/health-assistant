import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { uploadDocument } from '../../services/documentService';
import { createExamination, getExaminationCategories, createExaminationCategory } from '../../services/examinationService';
import { listDoctors, createDoctor, Doctor } from '../../services/doctorService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { offlineService } from '../../services/offlineService';
import { RichTextEditor } from '../../components/ui/RichTextEditor';
import { Check, X, FileText, RotateCcw, Sparkles, Plus, Info, Camera } from 'lucide-react';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { AIBadge } from '../../components/ui/AIBadge';
import { AIMagicFillModal } from '../../components/ui/AIMagicFillModal';
import { AIAssistButton } from '../../components/ui/AIAssistButton';
import { DoctorSelector } from '../../components/ui/DoctorSelector';
import { OrganizationSelector } from '../../components/ui/OrganizationSelector';
import { CategorySelector } from '../../components/ui/CategorySelector';
import { listOrganizations, createOrganization, Organization } from '../../services/organizationService';
import { DynamicIcon } from '../../components/ui/DynamicIcon';
import { ExaminationGroupManager, type ExamGroup, type FileWithGroup } from './ExaminationGroupManager';
import { isMobileDevice } from '../../utils/deviceUtils';
import { getTempPreviewUrl } from '../../services/documentService';
import { FilePreviewManager } from '../../components/ui/FilePreviewManager';
import { FileCard } from '../../components/ui/FileCard';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { DatePicker } from '../../components/ui/DatePicker';

function ExaminationUpload() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const showConfirmation = useUIStore(state => state.showConfirmation);
  const [files, setFiles] = useState<{file: File, include: boolean, groupIndex?: number}[]>([]);
  const [activePreview, setActivePreview] = useState<{ url: string; name: string; type: string; isBackendProcessed?: boolean; localFile?: File } | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [patientId, setPatientId] = useState('');
  const [examinationDate, setExaminationDate] = useState<string | undefined>(new Date().toISOString().split('T')[0]);
  const [notes, setNotes] = useState('');
  const [patientNotes, setPatientNotes] = useState('');
  const [category, setCategory] = useState('');
  const [dynamicCategories, setDynamicCategories] = useState<any[]>([]);
  const [isCustomCategory, setIsCustomCategory] = useState(false);
  const [availableDoctors, setAvailableDoctors] = useState<Doctor[]>([]);
  const [availableOrganizations, setAvailableOrganizations] = useState<Organization[]>([]);
  const [selectedDoctorIds, setSelectedDoctorIds] = useState<string[]>([]);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [hasDraft, setHasDraft] = useState(false);
  const [isMagicFillOpen, setIsMagicFillOpen] = useState(false);
  const [discoveredDoctors, setDiscoveredDoctors] = useState<string[]>([]);
  const [isCreatingDoctor, setIsCreatingDoctor] = useState(false);

  // New states for Smart Mode and Bulk Create
  const [isSmartMode, setIsSmartMode] = useState(false);
  const [isBulkMode, setIsBulkMode] = useState(false);
  
  // States for new Bulk Upload Manager
  const [bulkFiles, setBulkFiles] = useState<FileWithGroup[]>([]);
  const [bulkGroups, setBulkGroups] = useState<ExamGroup[]>([]);

  // Initialize first group if bulk mode or smart mode is activated
  useEffect(() => {
    if ((isBulkMode || isSmartMode) && bulkGroups.length === 0) {
      setBulkGroups([{
        id: Math.random().toString(36).substr(2, 9),
        name: 'Examination 1',
        date: new Date().toISOString().split('T')[0],
        category: 'Clinical',
        doctorIds: [],
        notes: '',
        patientNotes: ''
      }]);
    }
  }, [isBulkMode, isSmartMode]);

  // Sync legacy files with bulkFiles whenever legacy files change
  useEffect(() => {
    if (files.length > 0) {
      const bulkFileNames = new Set(bulkFiles.map(f => f.file.name));
      const newToSync = files.filter(f => !bulkFileNames.has(f.file.name));
      
      if (newToSync.length > 0) {
        const newBulkFiles = newToSync.map(f => ({
          id: Math.random().toString(36).substr(2, 9),
          file: f.file,
          groupId: isSmartMode ? (bulkGroups[0]?.id || null) : null,
          includeInExtraction: f.include
        }));
        setBulkFiles(prev => [...prev, ...newBulkFiles]);
      }
    }
  }, [files, isSmartMode, bulkGroups]);

  // Sync back from bulkFiles to legacy files when in manual mode
  useEffect(() => {
    if (!isSmartMode && !isBulkMode && bulkFiles.length > 0) {
      const legacyFileNames = new Set(files.map(f => f.file.name));
      const newToSync = bulkFiles.filter(f => !legacyFileNames.has(f.file.name));
      
      if (newToSync.length > 0) {
        const newLegacyFiles = newToSync.map(f => ({
          file: f.file,
          include: f.includeInExtraction
        }));
        setFiles(prev => [...prev, ...newLegacyFiles]);
      }
    }
  }, [bulkFiles, isSmartMode, isBulkMode]);

  // Auto-save draft logic (excluding Files for now to keep it lightweight)
  useEffect(() => {
    const draftData = {
      patientId,
      examinationDate,
      notes,
      patientNotes,
      category,
      selectedDoctorIds,
    };
    
    const timer = setTimeout(() => {
      if (notes || patientNotes || selectedDoctorIds.length > 0 || files.length > 0) {
        offlineService.saveDraft('new-examination', 'examination', draftData);
      }
    }, 2000);
    
    return () => clearTimeout(timer);
  }, [patientId, examinationDate, notes, patientNotes, category, selectedDoctorIds, files.length]);

  useEffect(() => {
    const checkDraft = async () => {
      const draft = await offlineService.getDraft('new-examination');
      if (draft) setHasDraft(true);
    };
    checkDraft();
  }, []);

  const restoreDraft = async () => {
    const draft = await offlineService.getDraft('new-examination');
    if (draft) {
      const { data } = draft;
      setPatientId(data.patientId);
      setExaminationDate(data.examinationDate);
      setNotes(data.notes || '');
      setPatientNotes(data.patientNotes || '');
      setCategory(data.category);
      setSelectedDoctorIds(data.selectedDoctorIds || []);
      setHasDraft(false);
    }
  };

  useEffect(() => {
    // If the user navigates here and a patient is globally selected, auto-fill it
    if (currentPatient?.id) {
      setPatientId(currentPatient.id);
    }
  }, [currentPatient]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [cats, docs, orgs] = await Promise.all([
          getExaminationCategories(),
          listDoctors(),
          listOrganizations()
        ]);
        setDynamicCategories(cats);
        setAvailableDoctors(docs);
        setAvailableOrganizations(orgs);
      } catch (err) {
        console.error('Failed to fetch initial data:', err);
      }
    };

    fetchData();
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files).map(file => ({ 
        file, 
        include: true, // Default to true if user just wants documents to be analyzed
        groupIndex: 0 
      }));
      setFiles((prev) => [...prev, ...newFiles]);
    }
    // Reset the input value so the same file can be selected again if needed
    e.target.value = '';
  };

  const toggleFileInclusion = (index: number) => {
    setFiles((prev) => prev.map((f, i) => i === index ? { ...f, include: !f.include } : f));
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const openPreview = async (file: File) => {
    const isDicom = file.name.toLowerCase().endsWith('.dcm');
    const isPdf = file.type === 'application/pdf';
    
    if (isDicom || isPdf) {
      setIsPreviewLoading(true);
      try {
        const result = await getTempPreviewUrl(file);
        setActivePreview({ 
          url: result.url, 
          name: file.name, 
          type: file.type,
          isBackendProcessed: true,
          localFile: file
        });
      } catch (err) {
        console.error("Failed to generate temp preview:", err);
        const url = URL.createObjectURL(file);
        setActivePreview({ url, name: file.name, type: file.type });
      } finally {
        setIsPreviewLoading(false);
      }
    } else {
      const url = URL.createObjectURL(file);
      setActivePreview({ url, name: file.name, type: file.type });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!patientId) {
      alert('Please select a patient from the top navigation bar before saving an examination.');
      return;
    }

    if (!navigator.onLine && (files.length > 0 || bulkFiles.length > 0)) {
       alert('You are currently offline. New examinations with file uploads require an active connection. Your current notes have been saved as a draft.');
       return;
    }

    if (isBulkMode) {
      const unassignedCount = bulkFiles.filter(f => f.groupId === null).length;
      if (unassignedCount > 0) {
        showConfirmation({
          title: 'Unassigned Documents',
          message: `You have ${unassignedCount} document(s) that are not assigned to any examination group. These will NOT be uploaded. Do you want to continue?`,
          confirmLabel: 'Upload Anyway',
          cancelLabel: 'Go Back',
          confirmVariant: 'danger',
          onConfirm: () => executeSubmit()
        });
        return;
      }
    }

    executeSubmit();
  };

  const executeSubmit = async () => {
    setUploading(true);
    try {
      if (isBulkMode) {
        // Bulk Create: Create multiple examinations based on groups
        const createdExams = [];

        for (const group of bulkGroups) {
          const groupFiles = bulkFiles.filter(f => f.groupId === group.id);
          // Only create examination if it has files OR has notes
          if (groupFiles.length === 0 && !group.notes && !group.patientNotes) continue;

          // Create Examination for this group
          const exam = await createExamination({
            patient_id: patientId,
            examination_date: isSmartMode ? undefined : group.date,
            notes: isSmartMode ? undefined : group.notes || undefined,
            patient_notes: group.patientNotes || undefined,
            category: isSmartMode ? 'Clinical' : group.category,
            doctor_ids: isSmartMode ? [] : group.doctorIds,
            organization_id: isSmartMode ? undefined : (selectedOrganizationId || undefined),
            auto_extract_metadata: isSmartMode
          });


          // Upload all files associated with this group's examination
          for (const { file, includeInExtraction } of groupFiles) {
            await uploadDocument(file, patientId, exam.id, includeInExtraction);
          }
          createdExams.push(exam);
        }
        
        // Clear draft
        await offlineService.deleteDraft('new-examination');
        
        // Redirect to the examination list for bulk uploads
        navigate('/examinations');
      } else {
        // Single Examination Create
        const exam = await createExamination({
          patient_id: patientId,
          examination_date: isSmartMode ? undefined : examinationDate,
          notes: isSmartMode ? undefined : notes || undefined,
          patient_notes: patientNotes || undefined, // Patient notes are always sent if present
          category: isSmartMode ? 'Clinical' : category,
          doctor_ids: isSmartMode ? [] : selectedDoctorIds,
          organization_id: isSmartMode ? undefined : (selectedOrganizationId || undefined),
          auto_extract_metadata: isSmartMode
        });

        // 2. Upload all files associated with this examination
        const filesToUpload = isSmartMode 
          ? bulkFiles.map(f => ({ file: f.file, include: f.includeInExtraction }))
          : files;

        for (const { file, include } of filesToUpload) {
          await uploadDocument(file, patientId, exam.id, include);
        }
        
        // Clear draft
        await offlineService.deleteDraft('new-examination');
        
        // Redirect to the examination detail page
        navigate(`/examinations/${exam.id}`);
      }
    } catch (error) {
      console.error('Upload failed:', error);
      alert('Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  };

  const handleAIFill = (data: any) => {
    if (data.examination_date) setExaminationDate(data.examination_date);
    if (data.notes) setNotes(data.notes);
    if (data.patient_notes) setPatientNotes(data.patient_notes);
    if (data.category) {
      setCategory(data.category);
      setIsCustomCategory(!dynamicCategories.find(c => c.name === data.category || c.slug === data.category));
    }
    if (data.doctor_names && data.doctor_names.length > 0) {
      const matchedIds: string[] = [];
      const unknown: string[] = [];

      data.doctor_names.forEach((rawName: string) => {
        const name = rawName.replace(/^(dr\.?\s*)+/i, '').trim();
        const match = availableDoctors.find(doc => 
          doc.name.toLowerCase().includes(name.toLowerCase()) || 
          name.toLowerCase().includes(doc.name.toLowerCase())
        );
        if (match) matchedIds.push(match.id);
        else unknown.push(name);
      });
      
      if (matchedIds.length > 0) {
        setSelectedDoctorIds(Array.from(new Set([...selectedDoctorIds, ...matchedIds])));
      }
      if (unknown.length > 0) {
        setDiscoveredDoctors(prev => Array.from(new Set([...prev, ...unknown])));
      }
    }
  };

  const handleCreateDoctor = async (name: string) => {
    setIsCreatingDoctor(true);
    try {
      const newDoc = await createDoctor({ name });
      setAvailableDoctors(prev => [...prev, newDoc]);
      setSelectedDoctorIds(prev => [...prev, newDoc.id]);
      setDiscoveredDoctors(prev => prev.filter(d => d !== name));
    } catch (err) {
      console.error("Failed to create doctor", err);
      alert("Failed to add doctor to system.");
    } finally {
      setIsCreatingDoctor(false);
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
      setCategory(newCat.name);
    } catch (err) {
      console.error("Failed to create category", err);
      alert("Failed to create category");
    }
  };

  const headerIcon = useMemo(() => <FileText className="w-8 h-8" />, []);

  if (!currentPatient) {
    return <NoPatientState icon={FileText} contextKey="examination_upload" />;
  }

  return (
    <div className={`${isBulkMode ? 'max-w-5xl' : 'max-w-2xl'} mx-auto transition-all duration-500`}>
      <PageHeader
        title="New Examination"
        subtitle="Upload and analyze clinical documents"
        icon={headerIcon}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center gap-3">
            {hasDraft && (
              <button 
                onClick={restoreDraft}
                className="flex items-center gap-2 px-4 py-2 bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400 rounded-xl text-sm font-bold border border-amber-100 dark:border-amber-800 transition-all hover:bg-amber-100"
              >
                <RotateCcw className="w-4 h-4" />
                <span>Restore Draft</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => setIsMagicFillOpen(true)}
              className="flex items-center gap-2 px-6 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-sm transition-all shadow-lg shadow-indigo-500/20 active:scale-95"
            >
              <Sparkles className="w-4 h-4" />
              <span>Magic Fill</span>
            </button>
          </div>
        }
      />

      <form onSubmit={handleSubmit} className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 space-y-6">
        {/* Smart Mode & Bulk Toggle */}
        <div className="flex flex-col md:flex-row gap-4 p-4 bg-blue-50/50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl">
          <div className="flex-1 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-sm font-bold text-gray-900 dark:text-dark-text flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-blue-500" />
                {t('ai_labels.smart_extraction_ai', 'Smart Extraction AI')}
                <AIBadge taskType="ocr" className="ml-2" />
              </span>
              <span className="text-[10px] text-gray-500 dark:text-dark-muted">{t('ai_labels.smart_extraction_hint', 'AI will auto-fill date, doctors, and notes from documents')}</span>
            </div>
            <button
              type="button"
              onClick={() => setIsSmartMode(!isSmartMode)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${isSmartMode ? 'bg-blue-600' : 'bg-gray-200 dark:bg-dark-border'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${isSmartMode ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <div className="w-px bg-blue-100 dark:bg-blue-900/30 hidden md:block" />
          <div className="flex-1 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-sm font-bold text-gray-900 dark:text-dark-text flex items-center gap-2">
                <FileText className="w-4 h-4 text-indigo-500" />
                Bulk Create
              </span>
              <span className="text-[10px] text-gray-500 dark:text-dark-muted">Upload and group documents for multiple examinations</span>
            </div>
            <button
              type="button"
              onClick={() => {
                const newBulk = !isBulkMode;
                setIsBulkMode(newBulk);
                if (newBulk) setIsSmartMode(true); // Bulk usually implies smart mode
              }}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${isBulkMode ? 'bg-indigo-600' : 'bg-gray-200 dark:bg-dark-border'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${isBulkMode ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
        </div>

        {isBulkMode && (
          <div className="flex items-center gap-2 p-3 bg-indigo-50/50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/20 rounded-xl">
            <Info className="w-4 h-4 text-indigo-500 flex-shrink-0" />
            <p className="text-[10px] text-indigo-700 dark:text-indigo-400 font-medium">
              <strong>Bulk Mode Instructions:</strong> Add documents to the top area, then drag them into examination bubbles below. You can create as many examination bubbles as needed. Each bubble represents a separate clinical visit.
            </p>
          </div>
        )}

        {isBulkMode || isSmartMode ? (
          <ExaminationGroupManager 
            files={bulkFiles}
            setFiles={setBulkFiles}
            groups={bulkGroups}
            setGroups={setBulkGroups}
            availableDoctors={availableDoctors}
            onAddDoctor={handleCreateDoctor}
            isSmartMode={isSmartMode}
            isSingleMode={!isBulkMode && isSmartMode}
            categories={dynamicCategories}
          />
        ) : (
          <>
            {!isSmartMode && (
              <div className="animate-in fade-in duration-300 space-y-6">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                    Examination Date
                  </label>
                  <DatePicker
                    required
                    value={examinationDate}
                    onChange={setExaminationDate}
                  />
                </div>
              </div>
            )}

            <div>
              <div className="flex items-center gap-2 mb-2">
                <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted">
                  Documents
                </label>
                <AIBadge workflow="full_reconstruction" size="sm" showText={false} />
              </div>
              
              {files.length > 0 && (
                <div className="mb-6 flex flex-wrap gap-4">
                  {files.map((f, i) => (
                    <FileCard 
                      key={i}
                      file={f.file}
                      onRemove={() => removeFile(i)}
                      onPreview={() => openPreview(f.file)}
                      onToggleInclusion={() => toggleFileInclusion(i)}
                      includeInExtraction={f.include}
                    />
                  ))}
                </div>
              )}

              <div className={`grid grid-cols-1 ${isMobileDevice() ? 'sm:grid-cols-2' : ''} gap-4`}>
                <div className="relative">
                  <input
                    type="file"
                    multiple
                    onChange={handleFileChange}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                    accept=".pdf,.jpg,.jpeg,.png,.docx,.txt,.dcm"
                    id="file-upload"
                  />
                  <label
                    htmlFor="file-upload"
                    className="flex items-center justify-center px-4 py-3 border-2 border-dashed border-gray-300 dark:border-dark-border rounded-xl hover:border-blue-500 dark:hover:border-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/10 transition-all cursor-pointer text-blue-600 dark:text-blue-400 font-bold text-sm"
                  >
                    <Plus className="h-5 w-5 mr-2" />
                    {files.length === 0 ? 'Select Documents' : 'Add More'}
                  </label>
                </div>
                
                {isMobileDevice() && (
                  <div className="relative">
                    <input
                      type="file"
                      accept="image/*"
                      capture="environment"
                      onChange={handleFileChange}
                      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                      id="camera-upload"
                    />
                    <label
                      htmlFor="camera-upload"
                      className="flex items-center justify-center px-4 py-3 border-2 border-dashed border-indigo-200 dark:border-indigo-900/30 rounded-xl hover:border-indigo-500 dark:hover:border-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/10 transition-all cursor-pointer text-indigo-600 dark:text-indigo-400 font-bold text-sm"
                    >
                      <Camera className="h-5 w-5 mr-2" />
                      Take Photo
                    </label>
                  </div>
                )}
              </div>
            </div>

            {!isSmartMode && (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500 space-y-6 mt-6">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted">
                      Clinical Notes (Optional)
                    </label>
                  </div>
                  <RichTextEditor 
                    value={notes} 
                    onChange={setNotes} 
                    placeholder="Add any clinical notes about this examination" 
                    minHeight="150px"
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between mb-2 px-1">
                      <label className="text-xs font-black text-gray-400 uppercase tracking-widest ml-1">
                        Category
                      </label>
                      <AIAssistButton 
                        taskType="magic_fill_examination"
                        context={{ patientId }}
                        showLabel={false}
                        placeholder="Describe the visit to categorize..."
                        onSuggestedData={(data) => {
                          if (data.category) {
                            setCategory(data.category);
                          }
                        }}
                      />
                    </div>
                    <CategorySelector
                      categories={dynamicCategories}
                      selectedName={category}
                      onSelect={(name) => setCategory(name)}
                      onCreate={handleCreateCategory}
                      placeholder={t('examination_detail.header.select_category')}
                    />
                  </div>

                  <div className="space-y-2">
                    <label className="text-xs font-black text-gray-400 uppercase tracking-widest ml-1">{t('organizations.title')}</label>
                    <OrganizationSelector
                      organizations={availableOrganizations}
                      selectedId={selectedOrganizationId}
                      onSelect={(id) => setSelectedOrganizationId(id)}
                      onCreate={handleCreateOrganization}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-black text-gray-400 uppercase tracking-widest ml-1">
                    Attending Doctors (Optional)
                  </label>
                  
                  {discoveredDoctors.length > 0 && (
                    <div className="mb-3 flex flex-wrap gap-2 animate-in fade-in slide-in-from-left-2 duration-300">
                      {discoveredDoctors.map(name => (
                        <button
                          key={name}
                          type="button"
                          disabled={isCreatingDoctor}
                          onClick={() => handleCreateDoctor(name)}
                          className="flex items-center gap-1.5 px-2.5 py-1 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg text-[10px] font-black uppercase tracking-widest border border-indigo-100 dark:border-indigo-900/30 hover:bg-indigo-100 transition-all shadow-sm"
                        >
                          <Sparkles className="w-3 h-3" />
                          <span>Add suggested: Dr. {name}</span>
                        </button>
                      ))}
                    </div>
                  )}

                  <DoctorSelector
                    doctors={availableDoctors}
                    selectedIds={selectedDoctorIds}
                    onSelect={(id) => setSelectedDoctorIds(prev => [...prev, id])}
                    onDeselect={(id) => setSelectedDoctorIds(prev => prev.filter(i => i !== id))}
                    onCreateDoctor={handleCreateDoctor}
                    placeholder={t('examinations.attending_physician')}
                  />
                </div>
              </div>
            )}

            {(!isBulkMode) && (
              <div className="animate-in fade-in slide-in-from-top-4 duration-500 space-y-6">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                    Patient Notes (Optional)
                  </label>
                  <textarea
                    value={patientNotes}
                    onChange={(e) => setPatientNotes(e.target.value)}
                    placeholder="How do you feel? Why did you visit the doctor?"
                    className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-dark-border dark:text-dark-text outline-none min-h-[80px]"
                  />
                </div>
              </div>
            )}
          </>
        )}

        {isSmartMode && !isBulkMode && (
          <div className="animate-in fade-in slide-in-from-top-4 duration-500 space-y-6">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-dark-muted mb-2">
                Patient Notes (Optional)
              </label>
              <textarea
                value={patientNotes}
                onChange={(e) => setPatientNotes(e.target.value)}
                placeholder="How do you feel? Why did you visit the doctor?"
                className="w-full px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-dark-border dark:text-dark-text outline-none min-h-[80px]"
              />
            </div>
          </div>
        )}

        <div className="flex space-x-4 pt-2">
          <button
            type="button"
            onClick={() => navigate('/examinations')}
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-dark-border rounded-lg hover:bg-gray-50 dark:hover:bg-dark-bg text-gray-700 dark:text-dark-text transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={uploading}
            className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm hover:shadow-md"
          >
            {uploading ? ((files.length > 0 || bulkFiles.length > 0) ? 'Creating & Uploading...' : 'Saving...') : (isBulkMode ? 'Save Examinations' : 'Save Examination')}
          </button>
        </div>
      </form>

      {/* Previews */}
      {isPreviewLoading && (
        <div className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-300">
           <div className="flex flex-col items-center gap-6">
              <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-indigo-500"></div>
              <p className="text-white font-black text-xs uppercase tracking-[0.3em] animate-pulse">Initializing Diagnostic Preview</p>
           </div>
        </div>
      )}

      {activePreview && (
        <FilePreviewManager 
          url={activePreview.url} 
          filename={activePreview.name} 
          type={activePreview.type} 
          isBackendProcessed={activePreview.isBackendProcessed}
          localFile={activePreview.localFile}
          onClose={() => {
            URL.revokeObjectURL(activePreview.url);
            setActivePreview(null);
          }} 
        />
      )}

      <AIMagicFillModal 
        isOpen={isMagicFillOpen} 
        onClose={() => setIsMagicFillOpen(false)} 
        onSuggestedData={handleAIFill}
        taskType="magic_fill_examination"
        context={{ patientId }}
        title="Magic Fill"
        subtitle="Clinical Extraction"
        description="Describe the visit in natural language. Health Assistant AI will extract the date, category, doctors, and notes automatically."
        placeholder="e.g. Yesterday I visited Dr. Smith for a checkup..."
      />
    </div>
  );
}

export default ExaminationUpload;
