import React, { useState, useEffect } from 'react';
import { ImageViewer } from './ImageViewer';
import { getDocumentDownloadUrl, getDocument } from '../../services/documentService';

interface AuthenticatedImageViewerProps {
  documentId: string;
  filename: string;
  onClose: () => void;
  category?: string;
  date?: string;
  gallery?: { id: string; title: string; type: string; category?: string; date?: string }[];
  onRefresh?: () => void;
}

export const AuthenticatedImageViewer: React.FC<AuthenticatedImageViewerProps> = ({ 
  documentId, filename, onClose, category, date, gallery, onRefresh 
}) => {
  const [currentId, setCurrentId] = useState(documentId);
  const [url, setUrl] = useState<string>('');
  const [docDetails, setDocDetails] = useState<{ parent_id?: string; is_edited?: boolean } | null>(null);
  
  const currentData = gallery?.find((img: any) => img.id === currentId) || {
    id: currentId,
    title: filename,
    category,
    date,
    type: 'document'
  };

  useEffect(() => {
    let isMounted = true;
    setUrl(''); // Clear current url while loading next
    
    const loadData = async () => {
      try {
        const fetchedUrl = await getDocumentDownloadUrl(currentId);
        if (!isMounted) return;
        setUrl(fetchedUrl);

        // Also fetch document details for editing features
        const details = await getDocument(currentId);
        if (isMounted) {
          setDocDetails({
            parent_id: (details as any).parent_id,
            is_edited: (details as any).is_edited
          });
        }
      } catch (err) {
        console.error("Failed to load image url for viewer:", err);
      }
    };

    loadData();
      
    return () => {
      isMounted = false;
    };
  }, [currentId]);

  if (!url) return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/95 backdrop-blur-md transition-all duration-300">
      <div className="flex flex-col items-center gap-6">
        <div className="relative">
          <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-blue-500"></div>
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
          </div>
        </div>
        <p className="text-white font-black text-xs uppercase tracking-[0.3em] animate-pulse">Loading clinical scan</p>
      </div>
    </div>
  );

  return (
    <ImageViewer 
      url={url} 
      filename={currentData.title} 
      category={currentData.category}
      date={currentData.date}
      relatedImages={gallery}
      currentId={currentId}
      parentId={docDetails?.parent_id}
      isEdited={docDetails?.is_edited}
      onSelectImage={(id) => setCurrentId(id)}
      onClose={onClose} 
      onRefresh={onRefresh}
    />
  );
};
