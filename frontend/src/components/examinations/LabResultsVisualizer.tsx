import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useBiomarkers } from '../../hooks/useBiomarkers';
import { Info, X } from 'lucide-react';
import biomarkerService from '../../services/biomarkerService';
import { Biomarker } from '../../types/biomarker';
import { getFinalStatus, formatUnit, formatBiomarkerValue } from '../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';

interface Props {
  documents: any[];
}

export default function LabResultsVisualizer({ documents }: Props) {
  const { biomarkers } = useBiomarkers({ documents });
  const precisionProfile = useBiomarkerPrecisionProfile();
  const [catalog, setCatalog] = useState<Record<string, Biomarker>>({});
  const [selectedInfo, setSelectedInfo] = useState<any>(null);

  const diagnoses: string[] = [];
  const medications: string[] = [];

  useEffect(() => {
    biomarkerService.getAllBiomarkers().then(data => {
      const map: Record<string, Biomarker> = {};
      data.forEach(b => {
        map[b.slug] = b;
      });
      setCatalog(map);
    }).catch(console.error);
  }, []);

  const augmentedBiomarkers = useMemo(() => {
    return biomarkers.map(b => {
      if (b.slug && catalog[b.slug]) {
        return { ...b, info: catalog[b.slug].info };
      }
      return b;
    });
  }, [biomarkers, catalog]);

  documents.forEach(doc => {
    if (doc.entities) {
      if (doc.entities.diagnoses && Array.isArray(doc.entities.diagnoses)) {
        doc.entities.diagnoses.forEach((d: string) => {
          if (!diagnoses.includes(d)) diagnoses.push(d);
        });
      }
      if (doc.entities.medications && Array.isArray(doc.entities.medications)) {
        doc.entities.medications.forEach((m: string) => {
          if (!medications.includes(m)) medications.push(m);
        });
      }
    }
  });

  if (biomarkers.length === 0 && diagnoses.length === 0 && medications.length === 0) {
    return (
      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-12 text-center">
        <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4">
          <svg className="w-8 h-8 text-gray-300 dark:text-dark-border" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        </div>
        <p className="text-gray-500 dark:text-dark-muted font-medium">No laboratory results extracted from these documents.</p>
        <p className="text-sm text-gray-400 dark:text-dark-border mt-1">Try uploading more clinical reports or check OCR status.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {biomarkers.length > 0 && (
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
          <div className="px-6 py-5 border-b border-gray-100 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/30">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">
              Laboratory Tests
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
              <thead className="bg-gray-50 dark:bg-dark-bg">
                <tr>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Test Name</th>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Value</th>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Unit</th>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Reference Range</th>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Method</th>
                  <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">Source</th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-dark-surface divide-y divide-gray-50 dark:divide-dark-border">
                {augmentedBiomarkers.map((b) => (
                  <tr key={b.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors group">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text flex items-center">
                      {b.definitionId ? (
                        <Link to={`/biomarkers/details/${b.definitionId}`} className="hover:text-blue-600 transition-colors">
                          {b.displayName}
                        </Link>
                      ) : (
                        <span>{b.displayName}</span>
                      )}
                      {b.info && (
                        <button 
                          onClick={() => setSelectedInfo(b)}
                          className="ml-2 p-1 text-blue-400 opacity-0 group-hover:opacity-100 transition-opacity hover:text-blue-600"
                          title="View Info"
                        >
                          <Info className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-blue-600 dark:text-blue-400 font-bold">
                      {formatBiomarkerValue(b.value.raw, precisionProfile)} {(() => {
                        const status = getFinalStatus(b as any);
                        return status !== 'Normal' ? (
                          <span className={`${status === 'High' ? 'text-red-500' : 'text-blue-500'} ml-1 text-xs uppercase font-black`}>
                            ({status})
                          </span>
                        ) : null;
                      })()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted font-medium">{formatUnit(b.unit.rawSymbol)}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted">{b.referenceRange.displayText}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-dark-muted">{b.method || '--'}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <Link to={`/documents/${b.source.documentId}`} className="text-blue-600 dark:text-blue-400 hover:underline font-medium">
                        {b.source.filename.length > 20 ? b.source.filename.substring(0, 20) + '...' : b.source.filename}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {diagnoses.length > 0 && (
          <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
            <h3 className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-4">Extracted Diagnoses</h3>
            <ul className="space-y-3">
              {diagnoses.map((d, idx) => (
                <li key={idx} className="flex items-start">
                  <div className="h-5 w-5 bg-blue-50 dark:bg-blue-900/30 rounded-full flex items-center justify-center mr-3 mt-0.5 flex-shrink-0">
                    <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                  </div>
                  <span className="text-sm font-semibold text-gray-700 dark:text-dark-text">{d}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {medications.length > 0 && (
          <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6">
            <h3 className="text-sm font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest mb-4">Medications Noted</h3>
            <ul className="space-y-3">
              {medications.map((m, idx) => (
                <li key={idx} className="flex items-start">
                  <div className="h-5 w-5 bg-green-50 dark:bg-green-900/30 rounded-full flex items-center justify-center mr-3 mt-0.5 flex-shrink-0">
                    <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                  </div>
                  <span className="text-sm font-semibold text-gray-700 dark:text-dark-text">{m}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Info Popup */}
      {selectedInfo && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-3xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
            <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                  <Info className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{selectedInfo.displayName}</h3>
                  <p className="text-xs text-gray-400 font-mono tracking-tighter uppercase">{selectedInfo.slug || 'Biomarker'}</p>
                </div>
              </div>
              <button onClick={() => setSelectedInfo(null)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <div className="p-8 max-h-[60vh] overflow-y-auto custom-scrollbar">
              <div className="prose dark:prose-invert max-w-none">
                <div 
                  className="text-gray-700 dark:text-dark-text leading-relaxed"
                  dangerouslySetInnerHTML={{ __html: selectedInfo.info }}
                />
              </div>
            </div>
            <div className="p-6 bg-gray-50 dark:bg-dark-bg border-t border-gray-100 dark:border-dark-border flex justify-end">
              <button 
                onClick={() => setSelectedInfo(null)}
                className="px-8 py-2.5 bg-[#1a2b4b] text-white rounded-xl font-bold text-sm hover:bg-black transition-all shadow-md active:scale-95"
              >
                Close Info
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
