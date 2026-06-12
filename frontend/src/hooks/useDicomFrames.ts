import { useState, useEffect, useRef, useCallback } from 'react';
import { getDocumentPreviewUrl, getTempPreviewUrl } from '../services/documentService';

interface UseDicomFramesProps {
  documentId?: string;
  localFile?: File;
  initialPage?: number;
}

export function useDicomFrames({ documentId, localFile, initialPage = 0 }: UseDicomFramesProps) {
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [totalPages, setTotalPages] = useState(1);
  const [currentUrl, setCurrentUrl] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const prefetchCache = useRef<Map<number, string>>(new Map());
  const isMounted = useRef(true);

  const cleanupCache = useCallback(() => {
    prefetchCache.current.forEach(u => {
      if (u.startsWith('blob:')) URL.revokeObjectURL(u);
    });
    prefetchCache.current.clear();
  }, []);

  // Set mounted ref
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
      cleanupCache();
    };
  }, [cleanupCache]);

  // Reset when source changes
  useEffect(() => {
    cleanupCache();
    setCurrentPage(initialPage);
    setTotalPages(1);
    setCurrentUrl('');
    setError(null);
  }, [documentId, localFile, initialPage, cleanupCache]);

  const fetchAndCacheFrame = useCallback(async (pageIndex: number) => {
    if (prefetchCache.current.has(pageIndex)) {
      return prefetchCache.current.get(pageIndex);
    }

    if (!documentId && !localFile) return null;

    try {
      let objectUrl = '';
      if (localFile) {
        const res = await getTempPreviewUrl(localFile, pageIndex);
        objectUrl = res.url;
        if (isMounted.current) setTotalPages(res.totalPages);
      } else if (documentId) {
        const previewObj = await getDocumentPreviewUrl(documentId, pageIndex);
        const response = await fetch(previewObj.url);
        if (!response.ok) throw new Error('Failed to fetch DICOM frame');
        
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        
        const total = parseInt(response.headers.get('X-Total-Pages') || '1');
        if (isMounted.current) setTotalPages(total);
      }

      if (objectUrl) {
        prefetchCache.current.set(pageIndex, objectUrl);
      }
      return objectUrl;
    } catch (err) {
      console.error(`Error loading DICOM frame ${pageIndex}:`, err);
      return null;
    }
  }, [documentId, localFile]);

  const loadFrame = useCallback(async (pageIndex: number) => {
    if (isLoading && prefetchCache.current.has(pageIndex)) {
        // If already loading but we want a cached one, just switch
        const url = prefetchCache.current.get(pageIndex);
        if (url) {
            setCurrentUrl(url);
            setCurrentPage(pageIndex);
            return;
        }
    }

    setIsLoading(true);
    setError(null);

    try {
      let url = prefetchCache.current.get(pageIndex);
      if (!url) {
        url = await fetchAndCacheFrame(pageIndex) || '';
      }

      if (isMounted.current && url) {
        setCurrentUrl(url);
        setCurrentPage(pageIndex);
        
        // Prefetch neighbors
        const next = pageIndex + 1;
        const prev = pageIndex - 1;
        
        if (next < totalPages) fetchAndCacheFrame(next);
        if (prev >= 0) fetchAndCacheFrame(prev);
      } else if (isMounted.current && !url) {
        setError('Failed to load frame');
      }
    } finally {
      if (isMounted.current) setIsLoading(false);
    }
  }, [fetchAndCacheFrame, isLoading, totalPages]);

  // Initial load
  useEffect(() => {
    loadFrame(currentPage);
  }, [documentId, localFile]);

  return {
    currentPage,
    totalPages,
    currentUrl,
    isLoading,
    error,
    loadFrame,
    nextFrame: () => currentPage < totalPages - 1 && loadFrame(currentPage + 1),
    prevFrame: () => currentPage > 0 && loadFrame(currentPage - 1)
  };
}
