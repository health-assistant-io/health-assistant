import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { getDocumentDownloadUrl } from '../../services/documentService';
import { AuthenticatedPdfViewer } from '../ui/AuthenticatedPdfViewer';

interface Props {
  documents: any[];
}

const Thumbnail = ({ doc, isSelected, onClick }: { doc: any, isSelected: boolean, onClick: () => void }) => {
  const [url, setUrl] = useState<string>('');
  
  useEffect(() => {
    getDocumentDownloadUrl(doc.id)
      .then(u => setUrl(u))
      .catch(console.error);
  }, [doc.id]);

  const isImage = /\.(jpg|jpeg|png|gif|webp)$/i.test(doc.filename);
  
  return (
    <div 
      onClick={onClick}
      className={`relative cursor-pointer flex-shrink-0 h-24 w-24 rounded-xl overflow-hidden border-2 transition-all ${isSelected ? 'border-blue-500 shadow-lg scale-105 z-10' : 'border-transparent hover:border-gray-200 dark:hover:border-dark-border opacity-60 hover:opacity-100'}`}
    >
      {url ? (
        isImage ? (
          <img src={url} alt={doc.filename} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full bg-gray-50 dark:bg-dark-bg flex flex-col items-center justify-center text-gray-400 dark:text-dark-muted">
             <svg className="w-8 h-8 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
             <span className="text-[10px] truncate w-full text-center px-1 font-bold uppercase tracking-tighter">{doc.filename.split('.').pop()?.toUpperCase()}</span>
          </div>
        )
      ) : (
        <div className="w-full h-full bg-gray-50 dark:bg-dark-bg flex items-center justify-center animate-pulse">
           <div className="w-4 h-4 border-2 border-blue-500 rounded-full animate-spin border-t-transparent"></div>
        </div>
      )}
    </div>
  );
};

export default function ImagingVisualizer({ documents }: Props) {
  const [selectedDoc, setSelectedDoc] = useState(documents[0] || null);
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [isPdfFullscreen, setIsPdfFullscreen] = useState(false);

  useEffect(() => {
    if (selectedDoc) {
      setPreviewUrl(''); // Reset while loading
      getDocumentDownloadUrl(selectedDoc.id)
        .then(url => setPreviewUrl(url))
        .catch(err => console.error("Failed to get presigned url", err));
    }
  }, [selectedDoc]);

  if (documents.length === 0) return null;

  const impressions = selectedDoc?.entities?.impressions_or_findings || "No specific impressions extracted. Please view the original document.";

  return (
    <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col">
      <div className="flex flex-col lg:flex-row h-full">
        {/* Left side: Document preview */}
        <div className="lg:w-2/3 border-b lg:border-b-0 lg:border-r border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/30 p-6 flex flex-col">
          
          <div className="w-full h-[550px] flex-shrink-0 flex items-center justify-center bg-white dark:bg-black rounded-2xl border border-gray-100 dark:border-dark-border overflow-hidden shadow-inner relative group">
              {selectedDoc && previewUrl ? (
                selectedDoc.filename.toLowerCase().endsWith('.pdf') ? (
                  <div className="w-full h-full">
                    <iframe 
                      src={`${previewUrl}#view=FitH`} 
                      className="w-full h-full border-0" 
                      title="PDF Preview"
                    />
                    <button 
                      onClick={() => setIsPdfFullscreen(true)}
                      className="absolute bottom-6 right-6 bg-black/70 backdrop-blur-md text-white px-4 py-2 rounded-xl text-xs font-bold opacity-0 group-hover:opacity-100 transition-all flex items-center gap-2 hover:bg-black/90 shadow-xl border border-white/10"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                      ENTER FULLSCREEN
                    </button>
                  </div>
                ) : (
                 <div className="w-full h-full flex items-center justify-center">
                   <img 
                     src={previewUrl} 
                     alt="Document Preview" 
                     className="max-w-full max-h-full object-contain"
                   />
                   <a href={previewUrl} target="_blank" rel="noopener noreferrer" className="absolute bottom-6 right-6 bg-black/70 backdrop-blur-md text-white px-4 py-2 rounded-xl text-xs font-bold opacity-0 group-hover:opacity-100 transition-all flex items-center gap-2 hover:bg-black/90 shadow-xl border border-white/10">
                     <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
                     VIEW FULL RESOLUTION
                   </a>
                 </div>
               )
             ) : (
               <div className="flex flex-col items-center justify-center">
                 <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 mb-4"></div>
                 <p className="text-xs font-bold text-gray-400 uppercase tracking-widest">Loading Imagery...</p>
               </div>
             )}
          </div>
          
          {/* Document Strip if multiple */}
          {documents.length > 1 && (
            <div className="mt-6 pt-6 border-t border-gray-100 dark:border-dark-border">
              <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted mb-3 uppercase tracking-[0.2em]">Imaging Series ({documents.length})</h4>
              <div className="flex gap-4 overflow-x-auto pb-4 custom-scrollbar">
                {documents.map(doc => (
                  <Thumbnail 
                    key={doc.id} 
                    doc={doc} 
                    isSelected={selectedDoc?.id === doc.id} 
                    onClick={() => setSelectedDoc(doc)} 
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right side: Findings */}
        <div className="lg:w-1/3 p-8 flex flex-col bg-white dark:bg-dark-surface">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text tracking-tight">AI Interpretation</h3>
            <Link to={`/documents/${selectedDoc.id}`} className="text-xs font-bold text-blue-600 dark:text-blue-400 hover:underline uppercase tracking-wider">
              Full Data
            </Link>
          </div>
          
          <div className="flex-1 bg-gray-50/50 dark:bg-dark-bg/30 border border-gray-100 dark:border-dark-border p-6 rounded-2xl overflow-y-auto custom-scrollbar">
            <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] mb-4">Impressions & Findings</h4>
            <p className="text-sm text-gray-700 dark:text-dark-text whitespace-pre-wrap leading-relaxed italic">
              "{impressions}"
            </p>
          </div>

          {selectedDoc?.entities?.diagnoses && selectedDoc.entities.diagnoses.length > 0 && (
             <div className="mt-6 pt-6 border-t border-gray-100 dark:border-dark-border">
               <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] mb-4">Key Diagnoses</h4>
               <div className="flex flex-wrap gap-2">
                 {selectedDoc.entities.diagnoses.map((d: string, i: number) => (
                   <span key={i} className="px-3 py-1.5 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-100 dark:border-red-900/30 rounded-lg text-xs font-bold">
                     {d}
                   </span>
                 ))}
               </div>
             </div>
          )}
        </div>
      </div>

      {isPdfFullscreen && selectedDoc && (
        <AuthenticatedPdfViewer 
          documentId={selectedDoc.id}
          filename={selectedDoc.filename}
          onClose={() => setIsPdfFullscreen(false)}
        />
      )}
    </div>
  );
}
