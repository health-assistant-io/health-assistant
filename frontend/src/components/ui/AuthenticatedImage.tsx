import { useState, useEffect } from 'react';
import { getDocumentPreviewUrl } from '../../services/documentService';

interface AuthenticatedImageProps {
  documentId: string;
  alt?: string;
  className?: string;
  fallbackText?: string;
}

export function AuthenticatedImage({ documentId, alt, className, fallbackText }: AuthenticatedImageProps) {
  const [src, setSrc] = useState<string>('');
  const [error, setError] = useState(false);

  useEffect(() => {
    let isMounted = true;
    
    if (documentId) {
      getDocumentPreviewUrl(documentId)
        .then(res => {
          if (isMounted) {
            setSrc(res.url);
          }
        })
        .catch(err => {
          console.error("Failed to load image url:", err);
          if (isMounted) setError(true);
        });
    }

    return () => {
      isMounted = false;
    };
  }, [documentId]);

  if (error) {
    return (
      <div className={`flex items-center justify-center bg-gray-100 text-gray-400 text-xs ${className}`}>
        {fallbackText || 'Image unavailable'}
      </div>
    );
  }

  if (!src) {
    return <div className={`animate-pulse bg-gray-200 ${className}`}></div>;
  }

  return <img src={src} alt={alt} className={className} />;
}
