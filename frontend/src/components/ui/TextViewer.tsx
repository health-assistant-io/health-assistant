import React, { useEffect } from 'react';
import { X, FileText } from 'lucide-react';

interface TextViewerProps {
  content?: string;
  url?: string;
  filename: string;
  onClose: () => void;
}

export const TextViewer: React.FC<TextViewerProps> = ({ content: initialContent, url, filename, onClose }) => {
  const [content, setContent] = React.useState(initialContent || '');
  const [loading, setLoading] = React.useState(!!url && !initialContent);

  useEffect(() => {
    if (url && !initialContent) {
      fetch(url)
        .then(res => res.text())
        .then(text => {
          setContent(text);
          setLoading(false);
        })
        .catch(err => {
          console.error("Failed to fetch text content:", err);
          setLoading(false);
        });
    }
  }, [url, initialContent]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const isMarkdown = filename.toLowerCase().endsWith('.md');

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col bg-black/95 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between p-4 bg-black/40 text-white">
        <div className="flex items-center space-x-4 overflow-hidden">
          <div className="p-2 bg-gray-800 rounded-lg">
            <FileText className="w-5 h-5 text-blue-400" />
          </div>
          <h2 className="text-lg font-bold truncate">{filename}</h2>
          <span className="px-2 py-0.5 bg-gray-700 text-[10px] font-bold uppercase rounded">
            {isMarkdown ? 'Markdown' : 'Text File'}
          </span>
        </div>
        <button 
          onClick={onClose}
          className="p-2 text-white/70 hover:text-white hover:bg-red-500 rounded-full transition-all"
          title="Close (Esc)"
        >
          <X className="w-6 h-6" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 w-full h-full p-4 sm:p-6 lg:p-8 flex justify-center overflow-hidden">
        <div className="w-full max-w-5xl h-full bg-white rounded-xl shadow-2xl overflow-y-auto p-8 sm:p-12">
          {isMarkdown ? (
            <div className="prose prose-blue max-w-none">
              {/* Very basic MD rendering if no lib is present - just preservation of line breaks and common spacing */}
              <pre className="whitespace-pre-wrap font-sans bg-transparent p-0 text-gray-800 border-none">
                {content}
              </pre>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-sm text-gray-800 leading-relaxed">
              {content}
            </pre>
          )}
        </div>
      </div>
      
      {/* Footer Info */}
      <div className="p-3 text-center text-white/40 text-[10px] uppercase tracking-widest">
        Secure Text Viewer • Escape to close
      </div>
    </div>
  );
};
