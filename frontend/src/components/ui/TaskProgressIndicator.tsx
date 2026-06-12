import { useState } from 'react';
import { Activity, Clock, FileText, AlertCircle, Terminal } from 'lucide-react';
import { TaskLogViewer } from './TaskLogViewer';

interface TaskProgressIndicatorProps {
  examinationId?: string;
  examinationStatus?: string;
  examinationProgress?: number;
  errorMessage?: string;
  documents?: any[];
  compact?: boolean;
}

export function TaskProgressIndicator({ 
  examinationId,
  examinationStatus, 
  examinationProgress, 
  errorMessage,
  documents,
  compact = false 
}: TaskProgressIndicatorProps) {
  const [isLogViewerOpen, setIsLogViewerOpen] = useState(false);
  
  // Check if there are any pending tasks or a failed state
  const hasPendingExtraction = examinationStatus && !['completed', 'failed'].includes(examinationStatus);
  const isFailed = examinationStatus === 'failed';
  
  // Only show OCR as "pending" in the main indicator if it's actually actively processing 
  // or if the document is selected for the AI extraction pipeline
  const hasPendingOCR = documents?.some(d => 
    d.status === 'processing' || 
    (d.status === 'uploaded' && d.include_in_extraction)
  );
  const hasFailedOCR = documents?.some(d => d.status === 'failed' && d.include_in_extraction);
  
  if (!hasPendingExtraction && !hasPendingOCR && !isFailed && !hasFailedOCR) {
    return null;
  }

  // Count documents for the progress bar - only those that are relevant to the current view
  const relevantDocs = documents?.filter(d => d.include_in_extraction || d.status === 'processing' || d.status === 'failed') || [];
  const pendingOCRCount = relevantDocs.filter(d => d.status !== 'completed' && d.status !== 'failed').length;
  const failedOCRCount = relevantDocs.filter(d => d.status === 'failed').length;
  const completedOCRCount = relevantDocs.filter(d => d.status === 'completed').length;
  const totalDocuments = relevantDocs.length;

  const getStatusLabel = (status?: string) => {
    if (!status) return '';
    const map: Record<string, string> = {
      'clinical_analysis': 'Phase 2: AI Analysis',
      'defining_ontology': 'Phase 3: Standardizing',
      'persisting_results': 'Phase 4: Saving',
      'aggregating': 'Preparing Data',
      'processing': 'Phase 1: OCR'
    };
    return map[status] || status.replace(/_/g, ' ');
  };

  if (compact) {
    return (
      <>
        <div className="flex items-center space-x-2 text-xs">
          {hasPendingExtraction && (
            <span className="flex items-center space-x-1 px-2 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg font-bold border border-blue-100 dark:border-blue-900/30">
              <Activity className="w-3 h-3 animate-pulse" />
              <span>{getStatusLabel(examinationStatus)} ({examinationProgress}%)</span>
            </span>
          )}
          {isFailed && (
            <span className="flex items-center space-x-1 px-2 py-1 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg font-bold border border-red-100 dark:border-red-900/30">
              <AlertCircle className="w-3 h-3" />
              <span>AI Failed</span>
            </span>
          )}
          {hasPendingOCR && (
            <span className="flex items-center space-x-1 px-2 py-1 bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded-lg font-bold border border-indigo-100 dark:border-indigo-900/30">
              <FileText className="w-3 h-3 animate-pulse" />
              <span>{pendingOCRCount}/{totalDocuments} OCR</span>
            </span>
          )}
          {hasFailedOCR && !hasPendingOCR && (
            <span className="flex items-center space-x-1 px-2 py-1 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg font-bold border border-red-100 dark:border-red-900/30">
              <FileText className="w-3 h-3" />
              <span>OCR Error</span>
            </span>
          )}
          {examinationId && (
            <button 
              onClick={() => setIsLogViewerOpen(true)}
              className="p-1 hover:bg-gray-100 dark:hover:bg-dark-bg rounded text-gray-400"
              title="View technical logs"
            >
              <Terminal className="w-3 h-3" />
            </button>
          )}
        </div>
        {examinationId && (
          <TaskLogViewer 
            examinationId={examinationId} 
            isOpen={isLogViewerOpen} 
            onClose={() => setIsLogViewerOpen(false)} 
          />
        )}
      </>
    );
  }

  return (
    <div className={`p-4 border rounded-xl ${isFailed ? 'bg-red-50/50 dark:bg-red-900/10 border-red-100 dark:border-red-900/20' : 'bg-blue-50/50 dark:bg-blue-900/10 border-blue-100 dark:border-blue-900/20'}`}>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-3">
          <div className={`p-2 rounded-lg ${isFailed ? 'bg-red-100 dark:bg-red-900/30' : 'bg-blue-100 dark:bg-blue-900/30 animate-pulse'}`}>
            {isFailed ? <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400" /> : <Activity className="w-4 h-4 text-blue-600 dark:text-blue-400" />}
          </div>
          <div>
            <h4 className={`text-xs font-bold uppercase tracking-widest ${isFailed ? 'text-red-900 dark:text-dark-text' : 'text-blue-900 dark:text-dark-text'}`}>
              {isFailed ? 'AI Extraction Failed' : (hasPendingExtraction ? 'AI Analysis in Progress' : 'Document Processing')}
            </h4>
            <p className={`text-[10px] font-bold uppercase ${isFailed ? 'text-red-600/70 dark:text-red-400/70' : 'text-blue-600/70 dark:text-blue-400/70'}`}>
              {isFailed ? (errorMessage || 'Check Task Monitor for details') : (hasPendingExtraction ? 'AI pipeline is running' : 'Extracting text from documents')}
            </p>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          {examinationId && (
            <button 
              onClick={() => setIsLogViewerOpen(true)}
              className="flex items-center space-x-1.5 px-3 py-1 bg-white/50 dark:bg-dark-bg/50 hover:bg-white dark:hover:bg-dark-bg border border-blue-100 dark:border-blue-900/30 rounded-lg text-[10px] font-black uppercase tracking-widest text-blue-600 dark:text-blue-400 transition-all"
            >
              <Terminal className="w-3 h-3" />
              <span>Technical Logs</span>
            </button>
          )}
          {!isFailed && <Clock className="w-4 h-4 text-blue-600/50 animate-spin-slow" />}
        </div>
      </div>

      {/* Examination Extraction Progress */}
      {(hasPendingExtraction || isFailed) && (
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1">
            <span className={`text-[10px] font-bold uppercase tracking-widest ${isFailed ? 'text-red-600' : 'text-blue-600'}`}>
              {getStatusLabel(examinationStatus)}
            </span>
            {!isFailed && (
              <span className="text-[10px] font-bold text-blue-600 uppercase">
                {examinationProgress}%
              </span>
            )}
          </div>
          {!isFailed && (
            <div className="h-1.5 bg-blue-200/50 dark:bg-blue-900/20 rounded-full overflow-hidden">
              <div 
                className="h-full bg-blue-600 transition-all duration-1000 ease-out"
                style={{ width: `${examinationProgress}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Document OCR Progress */}
      {(hasPendingOCR || hasFailedOCR) && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className={`text-[10px] font-bold uppercase tracking-widest ${hasFailedOCR && !hasPendingOCR ? 'text-red-600' : 'text-indigo-600'}`}>
              Document Processing
            </span>
            <span className="text-[10px] font-bold text-indigo-600 uppercase">
              {completedOCRCount}/{totalDocuments}
            </span>
          </div>
          
          <div className="h-1.5 bg-indigo-200/50 dark:bg-indigo-900/20 rounded-full overflow-hidden mb-4">
            <div 
              className={`h-full transition-all duration-1000 ease-out ${hasFailedOCR && !hasPendingOCR ? 'bg-red-600' : 'bg-indigo-600'}`}
              style={{ width: `${totalDocuments > 0 ? (completedOCRCount / totalDocuments) * 100 : 0}%` }}
            />
          </div>

          {/* Individual Document Progress Bars */}
          <div className="space-y-3 mt-4">
            {documents?.filter(d => d.status === 'processing' || d.status === 'failed').map((doc) => (
              <div key={doc.id} className="bg-white/30 dark:bg-dark-bg/30 p-2.5 rounded-lg border border-indigo-50/50 dark:border-indigo-900/10">
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center space-x-2 min-w-0">
                    <FileText className={`w-3 h-3 ${doc.status === 'failed' ? 'text-red-500' : 'text-indigo-500'}`} />
                    <span className="text-[10px] font-bold text-gray-700 dark:text-dark-text truncate">
                      {doc.filename}
                    </span>
                  </div>
                  <span className={`text-[9px] font-black uppercase ${doc.status === 'failed' ? 'text-red-600' : 'text-indigo-600'}`}>
                    {doc.status === 'failed' ? 'Failed' : `${doc.progress || 0}%`}
                  </span>
                </div>
                
                {doc.status === 'failed' ? (
                  <div className="text-[9px] text-red-500 font-bold leading-tight bg-red-50/50 dark:bg-red-900/20 p-1.5 rounded border border-red-100/50 dark:border-red-900/10 break-all">
                    Error: {doc.error_message || 'Unknown processing error'}
                  </div>
                ) : (
                  <div className="h-1 bg-indigo-100 dark:bg-indigo-900/30 rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-indigo-500 transition-all duration-500"
                      style={{ width: `${doc.progress || 0}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center space-x-2 mt-3">
            {pendingOCRCount > 0 && (
              <>
                <span className="text-[9px] font-bold text-indigo-600/70 uppercase">
                  {pendingOCRCount} pending
                </span>
                <AlertCircle className="w-3 h-3 text-indigo-600/50" />
              </>
            )}
            {failedOCRCount > 0 && (
              <span className="text-[9px] font-bold text-red-600/70 uppercase">
                {failedOCRCount} failed
              </span>
            )}
          </div>
        </div>
      )}

      {examinationId && (
        <TaskLogViewer 
          examinationId={examinationId} 
          isOpen={isLogViewerOpen} 
          onClose={() => setIsLogViewerOpen(false)} 
        />
      )}
    </div>
  );
}
