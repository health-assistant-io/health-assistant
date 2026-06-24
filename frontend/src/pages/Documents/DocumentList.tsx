import { useState, useEffect, useMemo } from 'react';
import { Search, Maximize2, ChevronLeft, ChevronRight, ChevronDown, FileText, Filter, ExternalLink, Activity, Download, Grid, List as ListIcon, ImageIcon, File, AlertCircle, CheckCircle2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { getDocuments, triggerDocumentDownload } from '../../services/documentService';
import { getExaminations, getExaminationCategories } from '../../services/examinationService';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { DynamicIcon } from '../../components/ui/DynamicIcon';
import { AuthenticatedImage } from '../../components/ui/AuthenticatedImage';
import { AuthenticatedPdf } from '../../components/ui/AuthenticatedPdf';
import { AuthenticatedImageViewer } from '../../components/ui/AuthenticatedImageViewer';
import { AuthenticatedDicomViewer } from '../../components/ui/AuthenticatedDicomViewer';
import { AuthenticatedDicomPreview } from '../../components/ui/AuthenticatedDicomPreview';
import { AuthenticatedPdfViewer } from '../../components/ui/AuthenticatedPdfViewer';
import { AuthenticatedTextViewer } from '../../components/ui/AuthenticatedTextViewer';
import { AuthenticatedText } from '../../components/ui/AuthenticatedText';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { MasterDetailLayout } from '../../components/ui/MasterDetailLayout';
import { useMasterDetail } from '../../hooks/useMasterDetail';
import { DocumentCard } from '../../components/documents/DocumentCard';
import { CategoryDropdown } from '../../components/ui/CategoryDropdown';
import { PageContainer } from '../../components/ui/PageContainer';

function DocumentList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [selectedCategories, setSelectedCategories] = useState<string[]>(['All']);
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDoc, setSelectedDoc] = useState<any>(null);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [dicomViewerOpen, setDicomViewerOpen] = useState(false);
  const [pdfViewerOpen, setPdfViewerOpen] = useState(false);
  const [textViewerOpen, setTextViewerOpen] = useState(false);
  const [fileTypeFilters, setFileTypeFilters] = useState<string[]>(['All']);
  const [isFilterMenuOpen, setIsFilterMenuOpen] = useState(false);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('list');

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  // Close menus when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      // Find elements with specific data attributes or just close if we click anywhere
      // Since the React tree might swallow events, a global listener on document is best
      setIsFilterMenuOpen(false);
    };

    if (isFilterMenuOpen) {
      // Small delay to prevent the initial click that opens the menu from immediately closing it
      setTimeout(() => {
        document.addEventListener('click', handleClickOutside);
      }, 0);
    }

    return () => {
      document.removeEventListener('click', handleClickOutside);
    };
  }, [isFilterMenuOpen]);
  const [examMap, setExamMap] = useState<Record<string, any>>({});
  const [dbCategories, setDbCategories] = useState<any[]>([]);
  const { currentPatient } = usePatientStore();

  const { isLargeScreen, handleItemClick, containerRef } = useMasterDetail({
    detailPath: (id) => `/documents/${id}`,
    onSelect: (id) => {
      const doc = documents.find(d => d.id === id);
      if (doc) setSelectedDoc(doc);
    }
  });
  
  const fetchData = async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [docs, exams, cats] = await Promise.all([
        getDocuments(),
        currentPatient?.id ? getExaminations(currentPatient.id) : Promise.resolve([]),
        getExaminationCategories()
      ]);
      
      setDbCategories(cats || []);
      
      let filteredDocs = docs;
      if (currentPatient?.id) {
        filteredDocs = docs.filter(d => d.patient_id === currentPatient.id);
      }
      
      const examMapping: Record<string, any> = {};
      for (const exam of exams) {
        examMapping[exam.id] = exam;
      }
      setExamMap(examMapping);
      
      setDocuments(filteredDocs);
      
      if (!selectedDoc && filteredDocs.length > 0 && window.innerWidth >= 1024) {
        setSelectedDoc(filteredDocs[0]);
      } else if (selectedDoc) {
        const synced = filteredDocs.find(d => d.id === selectedDoc.id);
        if (synced) setSelectedDoc(synced);
      }
    } catch (err) {
      console.error(err);
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  const getFileIcon = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase();
    if (ext?.match(/(png|jpe?g|webp|gif|bmp)$/)) return <ImageIcon className="w-5 h-5" />;
    if (ext === 'pdf') return <FileText className="w-5 h-5" />;
    if (ext?.match(/(txt|md)$/)) return <FileText className="w-5 h-5" />;
    return <File className="w-5 h-5" />;
  };

  useEffect(() => {
    fetchData(true);
  }, [currentPatient?.id]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    const hasProcessingDocs = documents.some(d => d.status === 'processing');

    if (hasProcessingDocs) {
      interval = setInterval(() => fetchData(false), 3000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [documents.some(d => d.status === 'processing'), currentPatient?.id]);

  const handleDownload = async (e: React.MouseEvent, id: string, filename: string) => {
    e.preventDefault();
    try {
      await triggerDocumentDownload(id, filename);
    } catch (err) {
      console.error("Failed to download file", err);
    }
  };

  const getDocCategory = (doc: any) => {
    if (doc.entities?.document_category) {
      if (typeof doc.entities.document_category === 'object' && doc.entities.document_category !== null) {
        return doc.entities.document_category.value || 'Other';
      }
      return doc.entities.document_category;
    }
    if (doc.examination_id && examMap[doc.examination_id]) {
      return examMap[doc.examination_id].category || 'Other';
    }
    return 'Other';
  };

  const tabsWithCounts = useMemo(() => {
    const counts: Record<string, number> = { 'All': documents.length };
    
    documents.forEach(doc => {
      const cat = getDocCategory(doc);
      if (cat) {
        counts[cat] = (counts[cat] || 0) + 1;
      }
    });

    const uniqueCategories = Array.from(new Set(
      documents.map(d => getDocCategory(d)).filter(Boolean)
    ));

    const sorted = uniqueCategories.filter(c => c !== 'Other');
    sorted.sort((a, b) => a.localeCompare(b));
    if (uniqueCategories.includes('Other')) {
      sorted.push('Other');
    }

    const nonEmpty = sorted.filter(cat => counts[cat] > 0);

    return [
      { name: t('common.view_all') as string, id: 'All', count: documents.length, icon: null, color: null },
      ...nonEmpty.map(name => {
        const catObj = dbCategories.find(c => c.name === name);
        return {
          name: t(`categories.${name}`, name) as string, // Translate if available
          id: name,
          count: counts[name] || 0,
          icon: catObj?.icon || null,
          color: catObj?.color || null
        };
      })
    ];
  }, [documents, dbCategories, examMap, t]);

  const filteredDocuments = documents.filter(d => {
    const matchesSearch = d.filename.toLowerCase().includes(searchTerm.toLowerCase());
    
    const category = getDocCategory(d);
    const matchesCategory = selectedCategories.includes('All') || selectedCategories.includes(category);
    
    const ext = d.filename.split('.').pop()?.toLowerCase();
    let matchesType = fileTypeFilters.includes('All');
    
    if (!matchesType) {
      if (fileTypeFilters.includes('Images') && !!ext?.match(/(png|jpe?g|webp|gif|bmp|tiff)$/i)) matchesType = true;
      if (fileTypeFilters.includes('PDF') && ext === 'pdf') matchesType = true;
      if (fileTypeFilters.includes('DICOM') && ext === 'dcm') matchesType = true;
      if (fileTypeFilters.includes('Text') && !!ext?.match(/(txt|md)$/i)) matchesType = true;
    }
    
    return matchesSearch && matchesCategory && matchesType;
  });

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

  const toggleFileType = (type: string) => {
    setFileTypeFilters(prev => {
      if (type === 'All') return ['All'];
      const filtered = prev.filter(t => t !== 'All');
      if (filtered.includes(type)) {
        const next = filtered.filter(t => t !== type);
        return next.length === 0 ? ['All'] : next;
      }
      return [...filtered, type];
    });
  };

  const goToNextDoc = () => {
    const currentIndex = filteredDocuments.findIndex(d => d.id === selectedDoc?.id);
    if (currentIndex < filteredDocuments.length - 1) {
      setSelectedDoc(filteredDocuments[currentIndex + 1]);
    }
  };

  const goToPrevDoc = () => {
    const currentIndex = filteredDocuments.findIndex(d => d.id === selectedDoc?.id);
    if (currentIndex > 0) {
      setSelectedDoc(filteredDocuments[currentIndex - 1]);
    }
  };

  if (!currentPatient) {
    return <NoPatientState icon={FileText} contextKey="documents" />;
  }

  if (loading) {
    return <LoadingState variant="section" showText={false} />;
  }

  const ListHeader = (
    <>
        <h3 className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">{t('documents_explorer.history')}</h3>
        <div className="flex items-center space-x-2">
           <div className="flex items-center bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-lg p-1">
              <button 
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md transition-all ${viewMode === 'grid' ? 'bg-gray-100 dark:bg-dark-bg shadow-sm text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <Grid className="w-4 h-4" />
              </button>
              <button 
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-md transition-all ${viewMode === 'list' ? 'bg-gray-100 dark:bg-dark-bg shadow-sm text-blue-600' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <ListIcon className="w-4 h-4" />
              </button>
           </div>
        </div>
    </>
  );

  const List = (
      <div className={`
        ${viewMode === 'grid' ? 'grid grid-cols-2 sm:grid-cols-3 gap-4' : 'flex flex-col space-y-4'} 
      `}>
        {filteredDocuments.map((doc) => {
          const category = getDocCategory(doc);
          const catObj = dbCategories.find(c => c.name === category);
          
          return (
            <DocumentCard
              key={doc.id}
              doc={doc}
              isSelected={selectedDoc?.id === doc.id}
              onClick={() => handleItemClick(doc.id, doc)}
              viewMode={isLargeScreen ? 'list' : viewMode}
              category={category}
              categoryDetails={catObj}
            />
          );
        })}
        
        {filteredDocuments.length === 0 && (
          <p className="text-sm text-gray-400 dark:text-dark-muted col-span-full">No documents found matching the criteria.</p>
        )}
      </div>
  );

  const Preview = selectedDoc ? (
    <div className="flex flex-col h-full overflow-hidden">
      <div className={`group bg-[#1a1c23] dark:bg-black overflow-hidden relative shadow-lg ${selectedDoc.filename.match(/\.(pdf|txt|md)$/i) ? 'flex flex-col h-[700px]' : 'min-h-[500px] flex items-center justify-center'}`}>
        {selectedDoc.filename.toLowerCase().endsWith('.dcm') ? (
          <AuthenticatedDicomPreview
            documentId={selectedDoc.id}
            className="max-h-[500px] object-contain"
          />
        ) : selectedDoc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i) ? (
          <AuthenticatedImage 
            documentId={selectedDoc.id} 
            className="max-h-[500px] object-contain" 
            alt="Scan" 
          />
        ) : selectedDoc.filename.match(/\.pdf$/i) ? (
          <div className="w-full h-full flex-1">
            <AuthenticatedPdf 
              documentId={selectedDoc.id} 
              className="w-full h-full"
            />
          </div>
        ) : selectedDoc.filename.match(/\.(txt|md)$/i) ? (
          <div className="w-full h-full flex-1 p-4">
            <AuthenticatedText 
              documentId={selectedDoc.id}
              filename={selectedDoc.filename}
              className="w-full h-full"
            />
          </div>
        ) : (
          <div className="text-gray-400 dark:text-dark-muted text-center">
            <FileText className="w-16 h-16 mx-auto mb-4 opacity-50" />
            <p>{t('documents_explorer.no_preview')}</p>
            <button onClick={(e) => handleDownload(e, selectedDoc.id, selectedDoc.filename)} className="text-blue-400 hover:text-blue-300 hover:underline mt-2 inline-block text-sm">{t('documents_explorer.download_file')}</button>
          </div>
        )}

        {filteredDocuments.length > 1 && (
          <>
            <button 
              onClick={(e) => { e.stopPropagation(); goToPrevDoc(); }}
              className="absolute left-4 top-1/2 -translate-y-1/2 p-3 bg-black/40 hover:bg-black/80 text-white rounded-full backdrop-blur-md opacity-0 group-hover:opacity-100 transition-all z-20 border border-white/5 shadow-2xl"
              title="Previous Document"
            >
              <ChevronLeft className="w-6 h-6" />
            </button>
            <button 
              onClick={(e) => { e.stopPropagation(); goToNextDoc(); }}
              className="absolute right-4 top-1/2 -translate-y-1/2 p-3 bg-black/40 hover:bg-black/80 text-white rounded-full backdrop-blur-md opacity-0 group-hover:opacity-100 transition-all z-20 border border-white/5 shadow-2xl"
              title="Next Document"
            >
              <ChevronRight className="w-6 h-6" />
            </button>
          </>
        )}
        
        <div className="absolute top-4 left-4 bg-black/60 backdrop-blur-sm text-white px-4 py-2 rounded-lg max-w-[60%] pointer-events-none">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
            {new Date(selectedDoc.created_at).toLocaleDateString()}
          </p>
          <p className="font-bold truncate">{selectedDoc.filename}</p>
        </div>
        
        <button 
          onClick={() => {
            if (selectedDoc.filename.toLowerCase().endsWith('.dcm')) setDicomViewerOpen(true);
            else if (selectedDoc.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i)) setViewerOpen(true);
            else if (selectedDoc.filename.match(/\.pdf$/i)) setPdfViewerOpen(true);
            else setTextViewerOpen(true);
          }}
          className="absolute top-4 right-4 bg-black/60 backdrop-blur-sm text-white p-2 rounded-lg hover:bg-black/80 transition-colors"
          title={t('common.view_original')}
        >
          <Maximize2 className="w-5 h-5" />
        </button>
        
        {!selectedDoc.filename.match(/\.pdf$/i) && !selectedDoc.filename.toLowerCase().endsWith('.dcm') && (
          <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 flex items-center bg-black/60 backdrop-blur-sm rounded-full p-1 border border-white/10 z-10">
            <button className="p-2 text-white hover:bg-white/20 rounded-full transition-colors"><ChevronLeft className="w-5 h-5" /></button>
            <div className="px-6 text-center">
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">{t('documents_explorer.scan')}</p>
              <p className="font-bold text-white text-sm">01/01</p>
            </div>
            <button className="p-2 text-white hover:bg-white/20 rounded-full transition-colors"><ChevronRight className="w-5 h-5" /></button>
            <div className="w-px h-8 bg-white/20 mx-2"></div>
            <div className="px-4 text-center min-w-[100px]">
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">{t('documents_explorer.status')}</p>
              <div className="flex flex-col items-center">
                <p className="font-bold text-white text-sm uppercase">
                  {selectedDoc.status}
                </p>
                {selectedDoc.status === 'processing' && (
                  <div className="w-full h-1 bg-white/20 rounded-full overflow-hidden mt-1">
                    <div 
                      className="h-full bg-blue-500 transition-all duration-500"
                      style={{ width: `${selectedDoc.progress || 0}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="p-6 lg:p-8 overflow-y-auto custom-scrollbar flex-1 min-h-0">
        <div className="flex flex-col sm:flex-row flex-wrap gap-4">
          {selectedDoc.examination_id && (
            <button 
              onClick={() => navigate(`/examinations/${selectedDoc.examination_id}`)}
              className="flex-1 min-w-[200px] flex items-center justify-between bg-purple-600 text-white p-5 rounded-2xl hover:bg-purple-700 transition-all group text-left shadow-lg shadow-purple-200/50 dark:shadow-none active:scale-95"
            >
              <div>
                <p className="text-[10px] font-bold text-purple-100 uppercase tracking-widest mb-1">{t('documents_explorer.origin')}</p>
                <p className="font-bold">{t('documents_explorer.view_examination')}</p>
              </div>
              <Activity className="w-6 h-6 text-white group-hover:scale-110 transition-transform" />
            </button>
          )}

          <button 
            onClick={() => navigate(`/documents/${selectedDoc.id}`)}
            className="flex-1 min-w-[200px] flex items-center justify-between bg-blue-50 dark:bg-blue-900/10 p-5 rounded-2xl border border-blue-100 dark:border-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all group text-left active:scale-95"
          >
            <div>
              <p className="text-[10px] font-bold text-blue-500 dark:text-blue-400 uppercase tracking-widest mb-1">{t('common.details')}</p>
              <p className="font-bold text-blue-700 dark:text-blue-300">{t('documents_explorer.full_details')}</p>
            </div>
            <ExternalLink className="w-6 h-6 text-blue-400 group-hover:scale-110 transition-transform" />
          </button>

          <button 
            onClick={(e) => handleDownload(e, selectedDoc.id, selectedDoc.filename)}
            className="flex-1 min-w-[200px] flex items-center justify-between bg-gray-50 dark:bg-dark-bg p-5 rounded-2xl border border-gray-100 dark:border-dark-border hover:bg-gray-100 dark:hover:bg-dark-border transition-all group text-left active:scale-95"
            title={t('documents_explorer.download_file')}
          >
            <div>
              <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-1">{t('common.download')}</p>
              <p className="font-bold text-gray-700 dark:text-dark-text">{t('documents_explorer.download_file')}</p>
            </div>
            <Download className="w-6 h-6 text-gray-400 group-hover:text-blue-500 transition-colors" />
          </button>
        </div>
      </div>
    </div>
  ) : (
    <div className="h-full flex flex-col items-center justify-center p-10 text-center opacity-30">
       <div className="w-20 h-20 bg-gray-100 dark:bg-dark-bg rounded-full flex items-center justify-center mb-6">
          <FileText className="w-10 h-10" />
       </div>
       <p className="text-lg font-black uppercase tracking-widest">{t('documents_explorer.select_to_view')}</p>
    </div>
  );

  return (
    <PageContainer>
      <PageHeader
        title={t('documents_explorer.title')}
        subtitle={t('documents_explorer.subtitle')}
        icon={<FileText className="w-8 h-8" />}
        breadcrumbs={[]}
      />

      <StickyToolbar
        className="flex-col sm:flex-row items-stretch sm:items-center"
        actions={
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full lg:w-auto flex-shrink-0 pt-2 sm:pt-0">
            <div className="relative">
               <button 
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsFilterMenuOpen(!isFilterMenuOpen);
                  }}
                  className={`w-full sm:w-auto flex items-center justify-between px-4 py-2 bg-white dark:bg-dark-surface rounded-xl border transition-all ${!fileTypeFilters.includes('All') ? 'border-blue-500 ring-2 ring-blue-500/10' : 'border-gray-200 dark:border-dark-border hover:border-blue-200'}`}
               >
                  <div className="flex items-center space-x-2">
                     <Filter className={`w-4 h-4 ${!fileTypeFilters.includes('All') ? 'text-blue-500' : 'text-gray-400'}`} />
                     <span className="text-sm font-bold text-gray-700 dark:text-dark-text whitespace-nowrap hidden sm:inline-block">
                        {fileTypeFilters.includes('All') ? t('common.file_type') : `${fileTypeFilters.length} Types`}
                     </span>
                  </div>
                  <ChevronDown className={`w-4 h-4 ml-2 text-gray-400 transition-transform ${isFilterMenuOpen ? 'rotate-180' : ''}`} />
               </button>

               {isFilterMenuOpen && (
                 <>
                   {/* Removed the fixed inset-0 overlay in favor of document click listener */}
                   <div 
                     className="absolute top-full left-0 sm:right-0 sm:left-auto mt-2 w-full sm:w-48 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-xl z-30 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200"
                     onClick={(e) => e.stopPropagation()} // Prevent clicking inside the menu from closing it
                   >
                      {['All', 'PDF', 'Images', 'DICOM', 'Text'].map((type) => (
                        <button
                          key={type}
                          onClick={() => toggleFileType(type)}
                          className={`w-full flex items-center justify-between px-4 py-3 text-sm font-bold transition-colors ${fileTypeFilters.includes(type) ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600' : 'text-gray-600 dark:text-dark-muted hover:bg-gray-50 dark:hover:bg-dark-bg'}`}
                        >
                          <div className="flex items-center space-x-3">
                            {type === 'All' && <Grid className="w-4 h-4" />}
                            {type === 'PDF' && <FileText className="w-4 h-4" />}
                            {type === 'Images' && <ImageIcon className="w-4 h-4" />}
                            {type === 'DICOM' && <Activity className="w-4 h-4" />}
                            {type === 'Text' && <FileText className="w-4 h-4" />}
                            <span>{type === 'All' ? t('common.view_all') : type}</span>
                          </div>
                          {fileTypeFilters.includes(type) && <CheckCircle2 className="w-4 h-4 text-blue-500" />}
                        </button>
                      ))}
                   </div>
                 </>
               )}
            </div>
          </div>
        }
      >
          <CategoryDropdown 
             tabs={tabsWithCounts} 
             selectedCategories={selectedCategories} 
             onToggleCategory={toggleCategory} 
             label={t('documents_explorer.categories')}
             allLabel={t('common.view_all')}
          />
      </StickyToolbar>

      <MasterDetailLayout 
        list={List}
        listHeader={ListHeader}
        detail={Preview}
        listWidth="lg:w-[400px] xl:w-[500px]"
        containerRef={containerRef}
        showDetail={isLargeScreen}
      />
      
      {viewerOpen && selectedDoc && (
        <AuthenticatedImageViewer 
          documentId={selectedDoc.id}
          filename={selectedDoc.filename}
          onClose={() => setViewerOpen(false)}
          onRefresh={fetchData}
        />
      )}

      {dicomViewerOpen && selectedDoc && (
        <AuthenticatedDicomViewer
          documentId={selectedDoc.id}
          filename={selectedDoc.filename}
          onClose={() => setDicomViewerOpen(false)}
          onRefresh={fetchData}
        />
      )}

      {pdfViewerOpen && selectedDoc && (
        <AuthenticatedPdfViewer 
          documentId={selectedDoc.id}
          filename={selectedDoc.filename}
          onClose={() => setPdfViewerOpen(false)}
        />
      )}

      {textViewerOpen && selectedDoc && (
        <AuthenticatedTextViewer 
          documentId={selectedDoc.id}
          filename={selectedDoc.filename}
          onClose={() => setTextViewerOpen(false)}
        />
      )}
    </PageContainer>
  );
}

export default DocumentList;
