import React, { useState, useEffect } from 'react';
import { ImageIcon } from 'lucide-react';
import { getDocumentPreviewUrl } from '../../services/documentService';

interface Props {
  documentId: string;
  filename: string;
  className?: string;
}

export const AuthenticatedThumbnail: React.FC<Props> = ({ documentId, filename, className }) => {
  const [url, setUrl] = useState<string>('');
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    getDocumentPreviewUrl(documentId)
      .then(res => {
        if (isMounted) {
          setUrl(res.url);
          setLoading(false);
        }
      })
      .catch(err => {
        console.error("Failed to load thumbnail:", err);
        if (isMounted) {
          setError(true);
          setLoading(false);
        }
      });
      
    return () => {
      isMounted = false;
    };
  }, [documentId]);

  const baseClasses = `max-w-full max-h-full ${className || 'object-cover'}`;

  if (loading) {
    return (
      <div className={`flex items-center justify-center bg-gray-50 animate-pulse w-full h-full ${className}`}>
        <ImageIcon className="w-8 h-8 text-gray-200" />
      </div>
    );
  }

  if (error || !url) {
    return (
      <div className={`flex items-center justify-center bg-gray-100 w-full h-full ${className}`}>
        <ImageIcon className="w-8 h-8 text-gray-400" />
      </div>
    );
  }

  return (
    <img 
      src={url} 
      alt={filename} 
      className={baseClasses}
      onError={() => setError(true)}
    />
  );
};
