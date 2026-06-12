import React, { useState, useEffect, useMemo } from 'react';
import { getDocumentDownloadUrl } from '../../services/documentService';
import { ImageViewer } from '../ui/ImageViewer';

interface ImagingAnalyticsProps {
  data: any;
  loading: boolean;
}

const DocumentPreview = ({ report, onOpenFullView }: { report: any, onOpenFullView: (url: string, filename: string) => void }) => {
  const [url, setUrl] = useState<string>('');
  
  useEffect(() => {
    getDocumentDownloadUrl(report.id)
      .then(u => setUrl(u))
      .catch(console.error);
  }, [report.id]);

  const filename = report.document_name || '';
  const isImage = /\.(jpg|jpeg|png|gif|webp)$/i.test(filename);
  const isPdf = /\.pdf$/i.test(filename);
  
  if (!url) {
    return (
      <div className="flex flex-col items-center justify-center h-48 w-full bg-gray-100 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border rounded-xl animate-pulse">
        <div className="w-8 h-8 border-b-2 border-blue-600 rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="relative group border border-gray-200 dark:border-dark-border rounded-xl overflow-hidden bg-white dark:bg-dark-surface hover:shadow-lg transition-all shadow-sm flex flex-col h-full">
      <div className="h-48 w-full bg-gray-50 dark:bg-black flex items-center justify-center overflow-hidden relative">
        {isImage ? (
          <img src={url} alt={filename} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
        ) : isPdf ? (
          <div className="flex flex-col items-center justify-center text-gray-400 dark:text-dark-muted h-full w-full">
            <svg className="w-12 h-12 mb-2 text-red-500/60" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
            <span className="text-xs font-bold uppercase tracking-widest opacity-50">PDF Report</span>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center text-gray-400 dark:text-dark-muted h-full w-full">
            <svg className="w-12 h-12 mb-2 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            <span className="text-xs font-bold uppercase tracking-widest opacity-50">Document</span>
          </div>
        )}
        
        {/* Overlay link */}
        <div className="absolute inset-0 w-full h-full bg-black/60 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-all duration-300 z-10">
          {isImage ? (
            <button 
              onClick={() => onOpenFullView(url, filename)} 
              className="px-4 py-2 bg-white dark:bg-dark-surface text-gray-900 dark:text-dark-text text-xs font-bold uppercase tracking-wider rounded-xl shadow-2xl transform translate-y-4 group-hover:translate-y-0 transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" /></svg>
              View Analysis
            </button>
          ) : (
            <a 
              href={url} 
              target="_blank" 
              rel="noopener noreferrer" 
              className="px-4 py-2 bg-white dark:bg-dark-surface text-gray-900 dark:text-dark-text text-xs font-bold uppercase tracking-wider rounded-xl shadow-2xl transform translate-y-4 group-hover:translate-y-0 transition-all hover:scale-105 active:scale-95 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
              Open File
            </a>
          )}
        </div>
      </div>
      
      <div className="p-4 bg-white dark:bg-dark-surface border-t border-gray-100 dark:border-dark-border z-20 relative flex-1">
        <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate" title={filename}>{filename}</p>
        <div className="flex items-center justify-between mt-2">
          <span className="text-[10px] font-black uppercase tracking-tighter text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-1.5 py-0.5 rounded">
            {report.type}
          </span>
        </div>
      </div>
    </div>
  );
};

const ImagingAnalytics: React.FC<ImagingAnalyticsProps> = ({ data, loading }) => {
  const [fullViewData, setFullViewData] = useState<{url: string, filename: string} | null>(null);

  // Apply overflow hidden to body when viewer is open to prevent background scrolling
  useEffect(() => {
    if (fullViewData) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [fullViewData]);

  const reports = data?.reports || [];
  const hasData = reports.length > 0;

  const groupedReports = useMemo(() => {
    const groups: Record<string, any[]> = {};
    reports.forEach((r: any) => {
      let d = r.date;
      let sortKey = r.date;
      try {
        const dateObj = new Date(r.date);
        if (!isNaN(dateObj.getTime())) {
          d = dateObj.toLocaleDateString(undefined, {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric'
          });
          sortKey = dateObj.toISOString().split('T')[0];
        }
      } catch (e) {
        // ignore
      }
      const key = `${sortKey}|${d}`;
      
      if (!groups[key]) groups[key] = [];
      groups[key].push(r);
    });
    
    return Object.entries(groups)
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([key, docs]) => ({
        label: key.split('|')[1],
        docs
      }));
  }, [reports]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (!hasData) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-lg shadow p-6 text-center">
        <p className="text-gray-500 dark:text-dark-muted">
          No matching imaging or radiology data found.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 relative">
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm px-8 py-6 border border-gray-100 dark:border-dark-border">
        <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text tracking-tight">Imaging Timeline & Galleries</h2>
        <p className="text-sm text-gray-500 dark:text-dark-muted mt-1">Chronological history of patient radiology and imaging scans</p>
      </div>
      
      <div className="relative border-l-2 border-gray-100 dark:border-dark-border ml-4 md:ml-10 space-y-12 pb-8 mt-10">
        {groupedReports.map((group, idx) => (
          <div key={idx} className="relative pl-8 md:pl-12">
            {/* Timeline Node */}
            <div className="absolute -left-[11px] top-1.5 h-5 w-5 rounded-full bg-blue-600 border-4 border-white dark:border-dark-surface shadow-md"></div>
            
            {/* Date Header */}
            <div className="mb-6">
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{group.label}</h3>
              <p className="text-xs font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mt-0.5">{group.docs.length} diagnostic scan{group.docs.length > 1 ? 's' : ''}</p>
            </div>
            
            {/* Image Gallery */}
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6 items-stretch">
              {group.docs.map((report: any) => (
                <DocumentPreview 
                  key={report.id} 
                  report={report} 
                  onOpenFullView={(url, filename) => setFullViewData({ url, filename })} 
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      
      {/* Full Screen Image Viewer Modal */}
      {fullViewData && (
        <ImageViewer 
          url={fullViewData.url} 
          filename={fullViewData.filename} 
          onClose={() => setFullViewData(null)} 
        />
      )}
    </div>
  );
};

export default ImagingAnalytics;
