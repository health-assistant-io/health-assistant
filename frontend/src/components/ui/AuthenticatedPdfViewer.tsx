import React, { useState, useEffect } from 'react';
import { PdfViewer } from './PdfViewer';
import { getDocumentDownloadUrl } from '../../services/documentService';

interface AuthenticatedPdfViewerProps {
  documentId: string;
  filename: string;
  onClose: () => void;
}

export const AuthenticatedPdfViewer: React.FC<AuthenticatedPdfViewerProps> = ({ documentId, filename, onClose }) => {
  const [url, setUrl] = useState<string>('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    getDocumentDownloadUrl(documentId)
      .then(fetchedUrl => {
        if (isMounted) {
          setUrl(fetchedUrl);
          setLoading(false);
        }
      })
      .catch(err => {
        console.error("Failed to load PDF url for viewer:", err);
        if (isMounted) setLoading(false);
      });
      
    return () => {
      isMounted = false;
    };
  }, [documentId]);

  if (loading) {
    return (
      <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/95 backdrop-blur-md transition-all duration-300">
        <div className="flex flex-col items-center">
          <div className="relative mb-6">
            <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-500"></div>
            <div className="absolute inset-0 flex items-center justify-center">
               <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
            </div>
          </div>
          <p className="text-white font-black text-xs uppercase tracking-[0.3em] animate-pulse">Loading secure document</p>
        </div>
      </div>
    );
  }

  if (!url) return null;

  return <PdfViewer url={url} filename={filename} onClose={onClose} />;
};
