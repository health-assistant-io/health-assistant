import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Database, RefreshCw, Upload, Globe, CheckCircle2, AlertCircle } from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { importCatalogFromUrl, importCatalogFromFile } from '../../services/adminService';

const CatalogManagement: React.FC = () => {
  const { t } = useTranslation();
  const [url, setUrl] = useState('https://raw.githubusercontent.com/ilias-ant/Health-Assistant-Catalogs/main/default-en.json');
  const [isImporting, setIsImporting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  const handleUrlImport = async () => {
    if (!url) return;
    setIsImporting(true);
    setMessage(null);
    try {
      const res = await importCatalogFromUrl(url);
      setMessage({ type: 'success', text: res.message });
    } catch (err: any) {
      setMessage({ type: 'error', text: err.response?.data?.detail || err.message || 'Import failed' });
    } finally {
      setIsImporting(false);
    }
  };

  const handleFileImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setIsImporting(true);
    setMessage(null);
    try {
      const res = await importCatalogFromFile(file);
      setMessage({ type: 'success', text: res.message });
    } catch (err: any) {
      setMessage({ type: 'error', text: err.response?.data?.detail || err.message || 'Import failed' });
    } finally {
      setIsImporting(false);
      if (e.target) e.target.value = ''; // Reset input
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6 animate-in fade-in duration-500">
      <PageHeader 
        title="Clinical Ontology Management" 
        icon={<Database className="w-8 h-8" />} 
      />

      <StickyToolbar />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* URL Import */}
        <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
          <div className="flex items-center space-x-3 mb-4">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <Globe className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">Import from URL</h3>
          </div>
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-4">
            Synchronize the system clinical ontology (LOINC biomarkers, units) from an external JSON catalog.
          </p>
          
          <div className="space-y-4">
            <input 
              type="url" 
              className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
              placeholder="https://..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={isImporting}
            />
            <button 
              onClick={handleUrlImport}
              disabled={isImporting || !url}
              className="w-full flex items-center justify-center space-x-2 px-6 py-3 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all disabled:opacity-50"
            >
              {isImporting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              <span>{isImporting ? 'Starting Import...' : 'Sync Catalog'}</span>
            </button>
          </div>
        </div>

        {/* File Import */}
        <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
          <div className="flex items-center space-x-3 mb-4">
            <div className="p-2 bg-purple-50 dark:bg-purple-900/30 rounded-xl">
              <Upload className="w-5 h-5 text-purple-600 dark:text-purple-400" />
            </div>
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">Upload JSON File</h3>
          </div>
          <p className="text-sm text-gray-500 dark:text-dark-muted mb-4">
            Upload a custom compiled JSON ontology file directly.
          </p>
          
          <div className="border-2 border-dashed border-gray-200 dark:border-dark-border rounded-2xl p-8 text-center mt-6 hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors relative">
             <input 
               type="file" 
               accept=".json"
               onChange={handleFileImport}
               disabled={isImporting}
               className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
             />
             <Upload className="w-8 h-8 text-gray-400 mx-auto mb-3" />
             <p className="text-sm font-bold text-gray-700 dark:text-dark-text">Click or drag file to upload</p>
             <p className="text-[10px] text-gray-400 uppercase tracking-widest mt-1">JSON format only</p>
          </div>
        </div>
      </div>

      {message && (
        <div className={`p-4 rounded-xl flex items-start space-x-3 ${
          message.type === 'success' 
            ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-900/30' 
            : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-900/30'
        }`}>
          {message.type === 'success' ? <CheckCircle2 className="w-5 h-5 flex-shrink-0" /> : <AlertCircle className="w-5 h-5 flex-shrink-0" />}
          <div>
             <h4 className="font-bold text-sm">{message.type === 'success' ? 'Import Started' : 'Import Failed'}</h4>
             <p className="text-xs opacity-90">{message.text}</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default CatalogManagement;