import { Link } from 'react-router-dom';

interface Props {
  documents: any[];
  categoryName?: string;
}

export default function ProcedureVisualizer({ documents }: Props) {
  if (documents.length === 0) return null;

  return (
    <div className="space-y-6">
      {documents.map((doc) => (
        <div key={doc.id} className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-border flex justify-between items-center bg-gray-50/50 dark:bg-dark-bg/30">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text tracking-tight">
              {doc.filename}
            </h3>
            <Link to={`/documents/${doc.id}`} className="text-xs font-bold text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 uppercase tracking-wider">
              View Analysis
            </Link>
          </div>
          
          <div className="p-8 grid grid-cols-1 xl:grid-cols-2 gap-8">
            <div className="space-y-4">
              <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em] mb-1">
                Extracted Clinical Findings
              </h4>
              <div className="bg-gray-50/50 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border p-5 rounded-2xl h-[300px] overflow-y-auto custom-scrollbar">
                <p className="text-sm text-gray-700 dark:text-dark-text leading-relaxed whitespace-pre-wrap italic">
                  "{doc.entities?.impressions_or_findings || "No detailed findings extracted. Please view the raw document."}"
                </p>
              </div>
            </div>

            <div className="space-y-8">
              {doc.entities?.diagnoses && doc.entities.diagnoses.length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">
                    Identified Diagnoses
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {doc.entities.diagnoses.map((d: string, idx: number) => (
                      <span key={idx} className="px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 border border-blue-100 dark:border-blue-900/30 rounded-lg text-xs font-bold">
                        {d}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {doc.entities?.medications && doc.entities.medications.length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-[0.2em]">
                    Medications Noted
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {doc.entities.medications.map((m: string, idx: number) => (
                      <span key={idx} className="px-3 py-1.5 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-100 dark:border-green-900/30 rounded-lg text-xs font-bold">
                        {m}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              
              {!doc.entities?.diagnoses?.length && !doc.entities?.medications?.length && (
                <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-dark-muted opacity-50 py-10">
                  <svg className="w-12 h-12 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg>
                  <p className="text-xs font-bold uppercase tracking-widest">No Structured Entities</p>
                </div>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
