import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getDocument, downloadDocument, getExtractionStatus, triggerExtraction, deleteDocument, updateDocument, triggerDocumentDownload } from '../../services/documentService';
import { getPatient } from '../../services/patientService';
import { CATEGORY_LABELS as CATEGORIES } from '../../constants/categories';
import { TaskProgressIndicator } from '../../components/ui/TaskProgressIndicator';
import { useBiomarkers } from '../../hooks/useBiomarkers';
import { getStatusColorClass, isAbnormal } from '../../utils/biomarkerUtils';
import { AuthenticatedText } from '../../components/ui/AuthenticatedText';
import { AuthenticatedImage } from '../../components/ui/AuthenticatedImage';
import { AuthenticatedPdf } from '../../components/ui/AuthenticatedPdf';
import { AuthenticatedImageViewer } from '../../components/ui/AuthenticatedImageViewer';
import { AuthenticatedDicomViewer } from '../../components/ui/AuthenticatedDicomViewer';
import { AuthenticatedPdfViewer } from '../../components/ui/AuthenticatedPdfViewer';
import { AuthenticatedTextViewer } from '../../components/ui/AuthenticatedTextViewer';
import { User, Activity, Clock, FileText, Database, Shield, Download, Trash2, RotateCw, Maximize2, MoreVertical, RefreshCw } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

export default function DocumentDetail() {
  const { t } = useTranslation();
  const { documentId } = useParams();
  const navigate = useNavigate();
  const [docData, setDocData] = React.useState<Record<string, any> | null>(null);
  const [patientData, setPatientData] = React.useState<any | null>(null);
  const [status, setStatus] = React.useState<string>('loading');
  const [isReprocessing, setIsReprocessing] = React.useState(false);
  const [fileUrl, setFileUrl] = React.useState<string | null>(null);
  const [showRawText, setShowRawText] = React.useState(false);
  const [viewerOpen, setViewerOpen] = React.useState(false);
  const [dicomViewerOpen, setDicomViewerOpen] = React.useState(false);
  const [pdfViewerOpen, setPdfViewerOpen] = React.useState(false);
  const [textViewerOpen, setTextViewerOpen] = React.useState(false);
  const [isEditingCategory, setIsEditingCategory] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<string>('');

  const { biomarkers } = useBiomarkers({ documents: docData ? [docData] : [] });

  React.useEffect(() => {
    const fetchData = async () => {
      if (!documentId) return;

      try {
        const doc = await getDocument(documentId);
        setDocData(doc);
        if (doc?.entities?.document_category) {
          setSelectedCategory(doc.entities.document_category);
        }

        if (doc.patient_id) {
          try {
            const patient = await getPatient(doc.patient_id);
            setPatientData(patient);
          } catch (pErr) {
            console.error("Failed to fetch patient info", pErr);
          }
        }
        
        // Fetch file blob for preview
        try {
          const blob = await downloadDocument(documentId);
          if (blob && blob.size > 0) {
            const url = window.URL.createObjectURL(blob);
            setFileUrl(url);
            console.log('File preview loaded:', doc.filename, blob.size);
          } else {
            console.warn('Empty blob received for preview');
          }
        } catch (previewErr) {
          console.error("Failed to load file preview", previewErr);
        }

        // Poll for extraction status
        const pollStatus = async () => {
          try {
            const docStatus = await getExtractionStatus(documentId);
            setStatus(docStatus.status);
            setDocData(prev => prev ? { ...prev, progress: docStatus.progress, error_message: docStatus.error_message, status: docStatus.status } : null);

            if (docStatus.status !== 'completed' && docStatus.status !== 'failed') {
              setTimeout(pollStatus, 2000);
            }
          } catch (err) {
            console.error('Error polling status:', err);
          }
        };

        pollStatus();
      } catch (error) {
        console.error('Failed to fetch document:', error);
        navigate('/documents');
      }
    };

    fetchData();
  }, [documentId, navigate]);

  // Cleanup object URL to prevent memory leaks
  React.useEffect(() => {
    return () => {
      if (fileUrl) {
        window.URL.revokeObjectURL(fileUrl);
      }
    };
  }, [fileUrl]);

  const handleDownload = async () => {
    if (!documentId || !docData) return;

    try {
      await triggerDocumentDownload(documentId, docData.filename || 'document.pdf');
    } catch (error) {
      console.error('Download failed:', error);
      alert("Failed to download file. Please try again.");
    }
  };

  const handleReprocess = async () => {
    if (!documentId) return;
    try {
      setIsReprocessing(true);
      await triggerExtraction(documentId);
      setStatus('processing');
      
      // Start polling again
      const pollStatus = async () => {
        try {
          const docStatus = await getExtractionStatus(documentId);
          setStatus(docStatus.status);
          setDocData(prev => prev ? { ...prev, progress: docStatus.progress, error_message: docStatus.error_message, status: docStatus.status } : null);

          if (docStatus.status !== 'completed' && docStatus.status !== 'failed') {
            setTimeout(pollStatus, 2000);
          } else {
            // Once completed, refresh the document data to get the new entities
            const freshDoc = await getDocument(documentId);
            setDocData(freshDoc);
            if (freshDoc?.entities?.document_category) {
              setSelectedCategory(freshDoc.entities.document_category);
            }
            setIsReprocessing(false);
          }
        } catch (err) {
          console.error('Error polling status:', err);
          setIsReprocessing(false);
        }
      };
      
      pollStatus();
    } catch (error) {
      console.error('Reprocess failed:', error);
      setIsReprocessing(false);
    }
  };

  const openFileViewer = () => {
    if (!docData) return;
    if (docData.filename.toLowerCase().endsWith('.dcm')) {
      setDicomViewerOpen(true);
    } else if (docData.filename.match(/\.(png|jpe?g|webp|gif|bmp)$/i)) {
      setViewerOpen(true);
    } else if (docData.filename.match(/\.pdf$/i)) {
      setPdfViewerOpen(true);
    } else {
      setTextViewerOpen(true);
    }
  };

  const handleDelete = async () => {
    if (!documentId) return;
    if (window.confirm('Are you sure you want to delete this document? This action cannot be undone.')) {
      try {
        await deleteDocument(documentId);
        navigate('/documents');
      } catch (error) {
        console.error('Delete failed:', error);
        alert('Failed to delete document');
      }
    }
  };

  const saveCategory = async () => {
    if (!documentId || !docData) return;
    try {
      const updatedEntities = {
        ...(docData.entities || {}),
        document_category: selectedCategory
      };
      
      const updatedDoc = await updateDocument(documentId, {
        entities: updatedEntities
      });
      
      setDocData(updatedDoc);
      setIsEditingCategory(false);
    } catch (error) {
      console.error('Failed to update category:', error);
      alert('Failed to update category');
    }
  };

  if (status === 'loading' && !docData) {
    return <div className="flex items-center justify-center h-screen">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={docData?.filename || 'Document'}
        subtitle={
          <div className="flex items-center space-x-2">
            <p className="text-sm text-gray-500 dark:text-dark-muted font-medium">Clinical Document ID: {documentId?.substring(0,8)}</p>
            <span className="text-gray-300 dark:text-dark-border">•</span>
            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
              status === 'completed' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' :
              status === 'failed' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' :
              'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 animate-pulse'
            }`}>
              {status === 'completed' ? t('common.success') : status === 'failed' ? t('common.error') : t('documents_explorer.processing')}
            </span>
          </div>
        }
        icon={<FileText className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('documents_explorer.repository'), path: '/documents' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center space-x-3">
            <button
              onClick={handleDownload}
              className="flex items-center px-4 py-2.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-all font-bold text-sm border border-blue-100 dark:border-blue-900/30 active:scale-95"
              title={t('common.download')}
            >
              <Download className="w-4 h-4 mr-2" />
              {t('common.download')}
            </button>

            <button
              onClick={handleReprocess}
              disabled={isReprocessing}
              className="flex items-center px-4 py-2.5 bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 rounded-xl hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-all font-bold text-sm border border-amber-100 dark:border-amber-900/30 disabled:opacity-50 active:scale-95"
              title={t('documents_explorer.reprocess')}
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${isReprocessing ? 'animate-spin' : ''}`} />
              {isReprocessing ? t('documents_explorer.processing') : t('documents_explorer.reprocess')}
            </button>
            
            <button
              onClick={handleDelete}
              className="flex items-center px-4 py-2.5 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-xl hover:bg-red-100 dark:hover:bg-red-900/40 transition-all font-bold text-sm border border-red-100 dark:border-red-900/30 active:scale-95"
            >
              <Trash2 className="w-4 h-4 mr-2" />
              {t('common.delete')}
            </button>
          </div>
        }
      />

      <TaskProgressIndicator 
        documents={docData ? [docData] : []}
        errorMessage={docData?.error_message}
      />

      {/* Main Viewer - Displayed by default */}
      <div className={`bg-[#1a1c23] dark:bg-black rounded-2xl overflow-hidden relative shadow-lg ${docData?.filename.match(/\.(pdf|txt|md)$/i) ? 'flex flex-col h-[700px]' : 'min-h-[500px] flex items-center justify-center'}`}>
        {docData ? (
          docData.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i) ? (
            <AuthenticatedImage 
              documentId={docData.id} 
              className="max-h-[700px] object-contain" 
              alt="Scan" 
            />
          ) : docData.filename.match(/\.pdf$/i) ? (
            <div className="w-full h-full flex-1">
              <AuthenticatedPdf 
                documentId={docData.id} 
                className="w-full h-full"
              />
            </div>
          ) : docData.filename.match(/\.(txt|md)$/i) ? (
            <div className="w-full h-full flex-1 p-4">
              <AuthenticatedText 
                documentId={docData.id}
                filename={docData.filename}
                className="w-full h-full"
              />
            </div>
          ) : (
            <div className="text-gray-400 dark:text-dark-muted text-center">
              <FileText className="w-16 h-16 mx-auto mb-4 opacity-50" />
              <p>Preview not available for this file type.</p>
              <button onClick={handleDownload} className="text-blue-400 hover:text-blue-300 hover:underline mt-2 inline-block text-sm">Download File</button>
            </div>
          )
        ) : (
          <div className="animate-pulse flex flex-col items-center">
            <div className="w-16 h-16 bg-gray-800 rounded-full mb-4"></div>
            <div className="h-4 w-48 bg-gray-800 rounded"></div>
          </div>
        )}
        
        {/* Viewer Overlay */}
        {docData && (
          <button 
            onClick={openFileViewer}
            className="absolute top-4 right-4 bg-black/60 backdrop-blur-sm text-white p-2 rounded-lg hover:bg-black/80 transition-colors z-10"
            title="Open Fullscreen"
          >
            <Maximize2 className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Main Action Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Examination */}
          {docData?.examination_id && (
            <button 
              onClick={() => navigate(`/examinations/${docData.examination_id}`)}
              className="flex items-center justify-between bg-white dark:bg-dark-surface p-6 rounded-2xl border border-gray-100 dark:border-dark-border hover:border-purple-200 dark:hover:border-purple-900/50 hover:bg-purple-50/30 dark:hover:bg-purple-900/5 transition-all group text-left shadow-sm"
            >
              <div className="flex items-center space-x-4">
                <div className="p-3 bg-purple-50 dark:bg-purple-900/20 rounded-xl text-purple-600 dark:text-purple-400 group-hover:scale-110 transition-transform">
                  <Activity className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-[10px] font-bold text-purple-500 dark:text-purple-400 uppercase tracking-widest mb-1">{t('documents_explorer.origin')}</p>
                  <p className="font-bold text-gray-900 dark:text-dark-text text-lg">{t('documents_explorer.view_examination')}</p>
                </div>
              </div>
              <div className="text-purple-300 dark:text-purple-900 group-hover:text-purple-500 transition-colors">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </button>
          )}

          {/* Patient */}
          {docData?.patient_id && (
            <button 
              onClick={() => navigate(`/patients/${docData.patient_id}`)}
              className="flex items-center justify-between bg-white dark:bg-dark-surface p-6 rounded-2xl border border-gray-100 dark:border-dark-border hover:border-blue-200 dark:hover:border-blue-900/50 hover:bg-blue-50/30 dark:hover:bg-blue-900/5 transition-all group text-left shadow-sm"
            >
              <div className="flex items-center space-x-4">
                <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-xl text-blue-600 dark:text-blue-400 group-hover:scale-110 transition-transform">
                  <User className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-[10px] font-bold text-blue-500 dark:text-blue-400 uppercase tracking-widest mb-1">{t('documents_explorer.context')}</p>
                  <p className="font-bold text-gray-900 dark:text-dark-text text-lg">{t('documents_explorer.patient_profile')}</p>
                </div>
              </div>
              <div className="text-blue-300 dark:text-blue-900 group-hover:text-blue-500 transition-colors">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </button>
          )}
      </div>

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
          <div className="space-y-1">
            <h3 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest flex items-center justify-between">
              {t('documents_explorer.categories')}
              {!isEditingCategory && (
                <button
                  onClick={() => setIsEditingCategory(true)}
                  className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 transition-colors"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                  </svg>
                </button>
              )}
            </h3>
            {isEditingCategory ? (
              <div className="flex items-center space-x-2 mt-2">
                <select
                  value={selectedCategory}
                  onChange={(e) => setSelectedCategory(e.target.value)}
                  className="block w-full rounded-lg border-gray-200 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-xs dark:bg-dark-bg dark:border-dark-border dark:text-dark-text"
                >
                  <option value="">Select category...</option>
                  {CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
                <button
                  onClick={saveCategory}
                  className="p-1.5 bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400 rounded-lg hover:bg-green-100 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                </button>
              </div>
            ) : (
              <p className="text-sm font-bold text-gray-900 dark:text-dark-text capitalize">
                {docData?.entities?.document_category || 'Uncategorized'}
                {docData?.entities?.document_sub_category && <span className="block text-[10px] text-gray-400 font-medium">({docData.entities.document_sub_category})</span>}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <h3 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.current_patient')}</h3>
            <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">
              {patientData ? (
                <span className="flex items-center">
                   {patientData.name?.given?.join(' ')} {patientData.name?.family}
                   <span className="ml-1 text-[10px] text-gray-400 font-medium">(MRN: {patientData.mrn || 'N/A'})</span>
                </span>
              ) : 'Unassigned'}
            </p>
          </div>
          <div className="space-y-1">
            <h3 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('common.account')}</h3>
            <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{docData?.owner_email || 'System'}</p>
          </div>
          <div className="space-y-1">
            <h3 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('examinations.metadata')}</h3>
            <p className="text-sm font-bold text-gray-900 dark:text-dark-text">
              {docData?.file_size ? (docData.file_size / 1024).toFixed(1) : '0'} KB
              <span className="ml-2 px-1.5 py-0.5 bg-gray-100 dark:bg-dark-bg text-[10px] rounded text-gray-500 uppercase">
                {docData?.filename?.split('.').pop()}
              </span>
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-8 pt-8 border-t border-gray-50 dark:border-dark-border">
          <div className="flex items-start space-x-3">
             <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400">
                <Clock className="w-4 h-4" />
             </div>
             <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('documents_explorer.audit_info')}</p>
                <div className="mt-1 space-y-0.5">
                   <p className="text-xs text-gray-600 dark:text-dark-text">{t('documents_explorer.created')}: <span className="font-bold">{docData?.created_at ? new Date(docData.created_at).toLocaleString() : '-'}</span></p>
                   <p className="text-xs text-gray-600 dark:text-dark-text">{t('documents_explorer.updated')}: <span className="font-bold">{docData?.updated_at ? new Date(docData.updated_at).toLocaleString() : '-'}</span></p>
                </div>
             </div>
          </div>
          <div className="flex items-start space-x-3">
             <div className="p-2 bg-purple-50 dark:bg-purple-900/20 rounded-lg text-purple-600 dark:text-purple-400">
                <Database className="w-4 h-4" />
             </div>
             <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('documents_explorer.storage_path')}</p>
                <p className="mt-1 text-xs font-mono text-gray-500 dark:text-dark-muted truncate max-w-[200px]" title={docData?.file_path}>
                   .../{docData?.file_path?.split('/').slice(-2).join('/')}
                </p>
             </div>
          </div>
          <div className="flex items-start space-x-3">
             <div className="p-2 bg-amber-50 dark:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400">
                <Shield className="w-4 h-4" />
             </div>
             <div>
                <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('documents_explorer.access_control')}</p>
                <p className="mt-1 text-xs text-gray-600 dark:text-dark-text">
                   {t('documents_explorer.resource_id')}: <span className="font-mono font-bold">{docData?.id?.substring(0,8)}...</span>
                   <br/>
                   {t('documents_explorer.extraction')}: <span className="font-bold">{docData?.include_in_extraction ? 'Enabled' : 'Disabled'}</span>
                </p>
             </div>
          </div>
        </div>
      </div>

      {status === 'completed' && (
        <div className="space-y-6">
          {biomarkers.length > 0 && (
            <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text tracking-tight mb-6">
                {t('documents_explorer.extracted_biomarkers')}
              </h2>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                  <thead className="bg-gray-50 dark:bg-dark-bg">
                    <tr>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarker_catalog.table.name')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.latest_result')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.standard_unit')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('biomarkers.clinical_reference')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('documents_explorer.method')}</th>
                    </tr>
                  </thead>
                  <tbody className="bg-white dark:bg-dark-surface divide-y divide-gray-50 dark:divide-dark-border">
                    {biomarkers.map((b) => (
                      <tr key={b.id} className="hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 transition-colors">
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text">{b.displayName}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-bold">
                          <span className="text-blue-600 dark:text-blue-400 mr-2">{b.value.raw}</span>
                          {isAbnormal(b.interpretation) && (
                            <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold border ${getStatusColorClass(b.interpretation)}`}>
                              {b.interpretation}
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted">{b.unit.rawSymbol}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted font-medium">{b.referenceRange.displayText}</td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-400 dark:text-dark-muted italic">{b.method || '--'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {docData?.entities && docData.entities.diagnoses && docData.entities.diagnoses.length > 0 && (
              <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
                <h2 className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-6">
                  {t('documents_explorer.diagnoses_found')}
                </h2>
                <ul className="space-y-4">
                  {docData.entities.diagnoses.map((d: string, idx: number) => (
                    <li key={idx} className="flex items-start">
                      <div className="h-5 w-5 bg-red-50 dark:bg-red-900/20 rounded-full flex items-center justify-center mr-3 mt-0.5 flex-shrink-0">
                        <div className="w-1.5 h-1.5 bg-red-500 rounded-full"></div>
                      </div>
                      <span className="text-sm font-bold text-gray-700 dark:text-dark-text leading-tight">{d}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {docData?.entities && docData.entities.medications && docData.entities.medications.length > 0 && (
              <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
                <h2 className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-6">
                  {t('documents_explorer.medications_noted')}
                </h2>
                <ul className="space-y-4">
                  {docData.entities.medications.map((m: string, idx: number) => (
                    <li key={idx} className="flex items-start">
                      <div className="h-5 w-5 bg-green-50 dark:bg-green-900/20 rounded-full flex items-center justify-center mr-3 mt-0.5 flex-shrink-0">
                        <div className="w-1.5 h-1.5 bg-green-500 rounded-full"></div>
                      </div>
                      <span className="text-sm font-bold text-gray-700 dark:text-dark-text leading-tight">{m}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {(docData?.extracted_text) && (
            <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-8">
              <h2 className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-6">
                {t('documents_explorer.clinical_text')}
              </h2>
              <div className="mt-4 border-l-4 border-blue-500 dark:border-blue-600 pl-4 py-2 bg-gray-50 dark:bg-dark-bg rounded-r-xl">
                <p className="text-gray-600 dark:text-dark-muted italic whitespace-pre-wrap max-h-96 overflow-y-auto custom-scrollbar text-sm leading-relaxed">
                  {docData.extracted_text || 'No text extracted yet or extraction pending.'}
                </p>
              </div>
              
              <div className="mt-6 flex flex-wrap gap-4">
                <button
                  onClick={() => setShowRawText(true)}
                  className="flex items-center px-6 py-2.5 bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 rounded-xl hover:bg-purple-100 dark:hover:bg-purple-900/40 transition-all text-xs font-bold uppercase tracking-wider border border-purple-100 dark:border-purple-900/30 active:scale-95"
                >
                  <FileText className="w-4 h-4 mr-2" />
                  {t('documents_explorer.view_raw_ocr')}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Full File Viewer Modals */}
      {viewerOpen && docData && (
        <AuthenticatedImageViewer 
          documentId={docData.id} 
          filename={docData.filename} 
          onClose={() => setViewerOpen(false)} 
          onRefresh={() => {
            // If we are on a document detail page and it gets edited, 
            // the original will be hidden in lists, but this page is for the specific ID.
            // We might want to refresh to see if it's now marked as edited.
            window.location.reload(); 
          }}
        />
      )}

      {dicomViewerOpen && docData && (
        <AuthenticatedDicomViewer
          documentId={docData.id}
          filename={docData.filename}
          onClose={() => setDicomViewerOpen(false)}
          onRefresh={() => window.location.reload()}
        />
      )}

      {pdfViewerOpen && docData && (
        <AuthenticatedPdfViewer 
          documentId={docData.id}
          filename={docData.filename}
          onClose={() => setPdfViewerOpen(false)}
        />
      )}

      {textViewerOpen && docData && (
        <AuthenticatedTextViewer 
          documentId={docData.id}
          filename={docData.filename}
          onClose={() => setTextViewerOpen(false)}
        />
      )}

      {/* Raw Extracted Text Modal */}
      {showRawText && docData?.extracted_text && (
        <div className="fixed inset-0 z-[1000] flex items-center justify-center overflow-y-auto overflow-x-hidden bg-black/50">
          <div className="relative w-full max-w-5xl p-4 mx-auto max-h-[90vh]">
            <div className="relative bg-white rounded-2xl shadow dark:bg-dark-surface flex flex-col max-h-[90vh]">
              <div className="flex items-center justify-between p-6 border-b rounded-t dark:border-dark-border shrink-0 bg-gray-50 dark:bg-dark-bg">
                <div>
                  <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">
                    Raw Extracted Text
                  </h3>
                  <p className="text-xs text-gray-500 dark:text-dark-muted mt-1">
                    OCR results and text extraction from document
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowRawText(false)}
                  className="text-gray-400 bg-transparent hover:bg-gray-200 hover:text-gray-900 rounded-lg text-sm w-8 h-8 inline-flex justify-center items-center dark:hover:bg-dark-border dark:hover:text-white"
                >
                  <svg className="w-3 h-3" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 14 14">
                    <path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="m1 1 6 6m0 0 6 6M7 7l6-6M7 7l-6 6"/>
                  </svg>
                  <span className="sr-only">Close modal</span>
                </button>
              </div>
              <div className="p-6 bg-gray-50 dark:bg-dark-bg overflow-y-auto flex-1">
                <div className="bg-white dark:bg-dark-surface rounded-xl border border-gray-200 dark:border-dark-border p-6">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-3">
                      <FileText className="w-5 h-5 text-blue-600" />
                      <span className="text-sm font-bold text-gray-700 dark:text-dark-text">Extracted Content</span>
                    </div>
                    <span className="text-xs text-gray-500 dark:text-dark-muted">
                      {docData.extracted_text.length} characters
                    </span>
                  </div>
                  <pre className="text-sm text-gray-700 dark:text-dark-muted whitespace-pre-wrap font-sans leading-relaxed max-h-[60vh] overflow-y-auto custom-scrollbar">
                    {docData.extracted_text}
                  </pre>
                </div>
                
                {/* Extraction Metadata */}
                <div className="mt-6 grid grid-cols-2 gap-4">
                  <div className="bg-blue-50 dark:bg-blue-900/20 rounded-xl border border-blue-100 dark:border-blue-900/30 p-4">
                    <h4 className="text-xs font-bold text-blue-700 dark:text-blue-400 uppercase tracking-widest mb-2">Extraction Info</h4>
                    <div className="space-y-1 text-xs text-gray-600 dark:text-dark-muted">
                      <p>Status: <span className="font-bold">{docData.status || 'N/A'}</span></p>
                      <p>Progress: <span className="font-bold">{docData.progress || 0}%</span></p>
                    </div>
                  </div>
                  <div className="bg-purple-50 dark:bg-purple-900/20 rounded-xl border border-purple-100 dark:border-purple-900/30 p-4">
                    <h4 className="text-xs font-bold text-purple-700 dark:text-purple-400 uppercase tracking-widest mb-2">Document Info</h4>
                    <div className="space-y-1 text-xs text-gray-600 dark:text-dark-muted">
                      <p>Size: <span className="font-bold">{docData?.file_size ? (docData.file_size / 1024).toFixed(1) : '0'} KB</span></p>
                      <p>Type: <span className="font-bold">{docData?.filename?.split('.').pop()}</span></p>
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex justify-end p-6 border-t dark:border-dark-border shrink-0 bg-gray-50 dark:bg-dark-bg">
                <button 
                  type="button" 
                  onClick={() => setShowRawText(false)} 
                  className="py-2.5 px-8 text-sm font-bold text-gray-700 bg-white dark:bg-dark-surface rounded-xl border border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-border transition-all active:scale-95"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
