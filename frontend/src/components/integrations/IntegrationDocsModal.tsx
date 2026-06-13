import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { X } from 'lucide-react';
import { integrationService } from '../../services/integrationService';
import { toast } from 'react-toastify';
import { LoadingState } from '../ui/LoadingState';
import { Portal } from '../ui/Portal';

interface IntegrationDocsModalProps {
  domain: string;
  onClose: () => void;
}

const IntegrationDocsModal: React.FC<IntegrationDocsModalProps> = ({ domain, onClose }) => {
  const [markdown, setMarkdown] = useState<string>('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchDocs = async () => {
      try {
        setLoading(true);
        const res = await integrationService.getDocumentation(domain);
        setMarkdown(res.markdown);
      } catch (error) {
        console.error('Failed to load documentation', error);
        toast.error('Failed to load documentation');
        setMarkdown('Failed to load documentation.');
      } finally {
        setLoading(false);
      }
    };
    
    fetchDocs();
  }, [domain]);

  return (
    <Portal>
      <div className="fixed inset-0 z-[9999] overflow-y-auto">
        <div className="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:p-0">
          <div className="fixed inset-0 transition-opacity bg-gray-900/80 backdrop-blur-sm" onClick={onClose} />

          <div className="relative inline-block w-full max-w-4xl p-8 overflow-hidden text-left align-middle transition-all transform bg-white dark:bg-dark-surface shadow-2xl rounded-2xl border border-gray-100 dark:border-dark-border mt-8 mb-8 z-[10000]">
            <div className="flex items-center justify-between mb-6 border-b border-gray-100 dark:border-dark-border pb-4">
              <h3 className="text-xl font-black text-gray-900 dark:text-dark-text capitalize">
                {domain.replace('_', ' ')} Documentation
              </h3>
              <button
                onClick={onClose}
                className="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 focus:outline-none"
              >
                <span className="sr-only">Close</span>
                <X className="w-6 h-6" />
              </button>
            </div>

            <div className="prose prose-blue max-w-none dark:prose-invert">
              {loading ? (
                <LoadingState />
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {markdown}
                </ReactMarkdown>
              )}
            </div>
          </div>
        </div>
      </div>
    </Portal>
  );
};

export default IntegrationDocsModal;
