import { useState, useEffect } from 'react';
import { getDocumentDownloadUrl } from '../../services/documentService';

interface AuthenticatedPdfProps {
  documentId: string;
  className?: string;
  fallbackText?: string;
}

export function AuthenticatedPdf({ documentId, className, fallbackText }: AuthenticatedPdfProps) {
  const [src, setSrc] = useState<string>('');
  const [error, setError] = useState(false);

  useEffect(() => {
    let isMounted = true;
    
    if (documentId) {
      getDocumentDownloadUrl(documentId)
        .then(url => {
          if (isMounted) {
            setSrc(url);
          }
        })
        .catch(err => {
          console.error("Failed to load PDF url:", err);
          if (isMounted) setError(true);
        });
    }

    return () => {
      isMounted = false;
    };
  }, [documentId]);

  if (error) {
    return (
      <div className={`flex items-center justify-center bg-[#1a1c23] text-gray-400 text-sm border border-gray-800 rounded-lg ${className}`}>
        {fallbackText || 'PDF preview unavailable'}
      </div>
    );
  }

  if (!src) {
    return <div className={`animate-pulse bg-gray-800 rounded-lg ${className}`}></div>;
  }

  return (
    <iframe 
      src={`${src}#toolbar=0&navpanes=0&view=FitH`} 
      className={`rounded-lg bg-white w-full h-full ${className}`}
      title="PDF Document Viewer"
      style={{ border: 'none', height: '100%', width: '100%', minHeight: '600px' }}
    />
  );
}
