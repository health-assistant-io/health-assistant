import { useState, useEffect } from 'react';
import { getDocumentDownloadUrl } from '../../services/documentService';

interface AuthenticatedTextProps {
  documentId: string;
  className?: string;
  filename: string;
}

export function AuthenticatedText({ documentId, className, filename }: AuthenticatedTextProps) {
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

  if (error) {
    return (
      <div className={`flex items-center justify-center bg-[#1a1c23] text-gray-400 text-sm border border-gray-800 rounded-lg ${className}`}>
        Failed to load file content
      </div>
    );
  }

  if (loading) {
    return <div className={`animate-pulse bg-gray-800 rounded-lg ${className}`}></div>;
  }

  const isMarkdown = filename.toLowerCase().endsWith('.md');

  return (
    <div className={`bg-white dark:bg-dark-surface rounded-lg overflow-y-auto p-6 max-h-[70vh] custom-scrollbar ${className}`}>
      {isMarkdown ? (
        <div className="prose dark:prose-invert prose-blue max-w-none">
          <pre className="whitespace-pre-wrap font-sans bg-transparent p-0 text-gray-800 dark:text-dark-text border-none">
            {content}
          </pre>
        </div>
      ) : (
        <pre className="whitespace-pre-wrap font-mono text-xs text-gray-800 dark:text-dark-text leading-relaxed">
          {content}
        </pre>
      )}
    </div>
  );
}
