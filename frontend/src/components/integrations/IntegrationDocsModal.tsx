import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { X, ChevronRight, BookOpen } from 'lucide-react';
import { integrationService, IntegrationDocsTreeCategory } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { LoadingState } from '../ui/LoadingState';
import { Portal } from '../ui/Portal';

interface IntegrationDocsModalProps {
  domain: string;
  onClose: () => void;
}

const IntegrationDocsModal: React.FC<IntegrationDocsModalProps> = ({ domain, onClose }) => {
  const [markdown, setMarkdown] = useState<string>('');
  const [tree, setTree] = useState<IntegrationDocsTreeCategory[] | undefined>(undefined);
  const [currentFile, setCurrentFile] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDocs = async () => {
      try {
        setLoading(true);
        const res = await integrationService.getDocumentation(domain, currentFile);
        setMarkdown(res.markdown);
        if (!tree) {
          setTree(res.tree);
        }
      } catch (error) {
        console.error('Failed to load documentation', error);
        toast.error('Failed to load documentation');
        setMarkdown('Failed to load documentation.');
      } finally {
        setLoading(false);
      }
    };
    
    fetchDocs();
  }, [domain, currentFile]); // Re-fetch when domain or currentFile changes

  const handleSelectFile = (file: string) => {
    if (file !== currentFile) {
      setCurrentFile(file);
    }
  };

  return (
    <Portal>
      <div className="fixed inset-0 z-[9999] overflow-y-auto">
        <div className="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:p-0">
          <div className="fixed inset-0 transition-opacity bg-gray-900/80 backdrop-blur-sm" onClick={onClose} />

          <div className="relative flex flex-col w-full max-w-5xl overflow-hidden text-left align-middle transition-all transform bg-white dark:bg-dark-surface shadow-2xl rounded-2xl border border-gray-100 dark:border-dark-border mt-8 mb-8 z-[10000]" style={{ height: '85vh' }}>
            <div className="flex items-center justify-between px-8 py-6 border-b border-gray-100 dark:border-dark-border bg-white dark:bg-dark-surface z-10">
              <h3 className="text-xl font-black text-gray-900 dark:text-dark-text capitalize flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-primary-600 dark:text-primary-400" />
                {domain.replace('_', ' ')} Documentation
              </h3>
              <button
                onClick={onClose}
                className="p-2 text-gray-400 hover:text-gray-500 hover:bg-gray-100 dark:hover:bg-dark-border rounded-full transition-colors dark:hover:text-gray-300 focus:outline-none"
              >
                <span className="sr-only">Close</span>
                <X className="w-6 h-6" />
              </button>
            </div>

            <div className="flex flex-1 overflow-hidden">
              {/* Sidebar (Only shown if tree exists) */}
              {tree && tree.length > 0 && (
                <div className="w-64 border-r border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-surface overflow-y-auto hidden md:block">
                  <div className="p-4 space-y-6">
                    {tree.map((category, idx) => (
                      <div key={idx} className="space-y-2">
                        <h4 className="text-xs font-bold tracking-wider text-gray-500 dark:text-gray-400 uppercase">
                          {category.category}
                        </h4>
                        <ul className="space-y-1">
                          {category.items.map((item) => {
                            const isSelected = currentFile === item.file || (!currentFile && idx === 0 && category.items.indexOf(item) === 0);
                            return (
                              <li key={item.id}>
                                <button
                                  onClick={() => handleSelectFile(item.file)}
                                  className={`w-full flex items-center justify-between px-3 py-2 text-sm rounded-lg transition-colors ${
                                    isSelected
                                      ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300 font-medium'
                                      : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-dark-border'
                                  }`}
                                >
                                  {item.title}
                                  {isSelected && <ChevronRight className="w-4 h-4" />}
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Main Content */}
              <div className="flex-1 overflow-y-auto p-8 relative">
                {loading && (
                  <div className="absolute inset-0 bg-white/50 dark:bg-dark-surface/50 backdrop-blur-sm flex items-center justify-center z-10">
                    <LoadingState />
                  </div>
                )}
                <div className="prose prose-blue max-w-none dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {markdown}
                  </ReactMarkdown>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Portal>
  );
};

export default IntegrationDocsModal;
