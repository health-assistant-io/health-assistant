import React, { useState, useEffect } from 'react';
import { DicomViewer } from './DicomViewer';
import { getDocumentPreviewUrl, getDocument } from '../../services/documentService';

interface AuthenticatedDicomViewerProps {
  documentId: string;
  filename: string;
  onClose: () => void;
  category?: string;
  date?: string;
  gallery?: { id: string; title: string; type: string; category?: string; date?: string }[];
  onRefresh?: () => void;
}

export const AuthenticatedDicomViewer: React.FC<AuthenticatedDicomViewerProps> = ({ 
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
        const fetchedData = await getDocumentPreviewUrl(currentId);
        if (!isMounted) return;
        setUrl(fetchedData.url);

        // Also fetch document details
        const details = await getDocument(currentId);
        if (isMounted) {
          setDocDetails({
            parent_id: (details as any).parent_id,
            is_edited: (details as any).is_edited
          });
        }
      } catch (err) {
        console.error("Failed to load DICOM url for viewer:", err);
      }
    };

    loadData();
      
    return () => {
      isMounted = false;
    };
  }, [currentId]);

  if (!url) return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-[#050505] backdrop-blur-md transition-all duration-300">
      <div className="flex flex-col items-center gap-6">
        <div className="relative">
          <div className="animate-spin rounded-full h-20 w-20 border-t-2 border-b-2 border-indigo-500"></div>
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-pulse"></div>
          </div>
        </div>
        <div className="flex flex-col items-center gap-1">
          <p className="text-white font-black text-xs uppercase tracking-[0.4em] animate-pulse">Initializing PACS Stream</p>
          <p className="text-gray-600 font-bold text-[9px] uppercase tracking-widest">Decoding Medical Data Matrix</p>
        </div>
      </div>
    </div>
  );

  return (
    <DicomViewer 
      url={url} 
      filename={currentData.title} 
      documentId={currentId}
      category={currentData.category}
      date={currentData.date}
      relatedImages={gallery}
      currentId={currentId}
      onSelectImage={(id) => setCurrentId(id)}
      onClose={onClose} 
      onRefresh={onRefresh}
    />
  );
};
