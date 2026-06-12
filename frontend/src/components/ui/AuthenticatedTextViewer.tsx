import React, { useState, useEffect } from 'react';
import { TextViewer } from './TextViewer';
import { getDocumentDownloadUrl } from '../../services/documentService';

interface AuthenticatedTextViewerProps {
  documentId: string;
  filename: string;
  onClose: () => void;
}

export const AuthenticatedTextViewer: React.FC<AuthenticatedTextViewerProps> = ({ documentId, filename, onClose }) => {
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let isMounted = true;
    
    const fetchContent = async () => {
      try {
        const url = await getDocumentDownloadUrl(documentId);
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to fetch content');
        const text = await response.text();
        
        if (isMounted) {
          setContent(text);
          setLoading(false);
        }
      } catch (err) {
        console.error("Failed to load text content:", err);
        if (isMounted) {
          setError(true);
          setLoading(false);
        }
      }
    };

    fetchContent();
      
    return () => {
      isMounted = false;
    };
  }, [documentId]);

  if (loading) {
    return (
      <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/95 backdrop-blur-sm">
        <div className="flex flex-col items-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mb-4"></div>
          <p className="text-white/50 text-sm animate-pulse">Decrypting content...</p>
        </div>
      </div>
    );
  }

  if (error || !content) {
    // Basic error view
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/95">
        <div className="text-center text-white">
          <p className="mb-4">Failed to load file content.</p>
          <button onClick={onClose} className="px-4 py-2 bg-gray-800 rounded-lg">Close</button>
        </div>
      </div>
    );
  }

  return <TextViewer content={content} filename={filename} onClose={onClose} />;
};
