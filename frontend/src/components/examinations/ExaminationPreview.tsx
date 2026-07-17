import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { 
  FileText, ArrowRight, ExternalLink, ClipboardList, User, 
  Activity, Bookmark, Pill, Stethoscope, BriefcaseMedical, 
  Info, Image as ImageIcon, Search, Download
} from 'lucide-react';
import { TaskProgressIndicator } from '../ui/TaskProgressIndicator';
import { AssociatedEvents } from '../events/AssociatedEvents';
import { ExaminationAIActions } from '../ui/ExaminationAIActions';
import { MedicationAIActions } from '../ui/MedicationAIActions';
import { AuthenticatedThumbnail } from '../ui/AuthenticatedThumbnail';
import { AIBadge } from '../ui/AIBadge';
import { isAbnormal, formatBiomarkerValue } from '../../utils/biomarkerUtils';
import { useBiomarkerPrecisionProfile } from '../../hooks/useBiomarkerPrecision';
import { stripHtml } from '../../utils/examinationUtils';
import { useBiomarkers } from '../../hooks/useBiomarkers';
import { Biomarker, BiomarkerObservation } from '../../types/biomarker';
import biomarkerService from '../../services/biomarkerService';

interface ExaminationPreviewProps {
  selectedExam: any;
  examDocuments: any[];
  onDocumentClick: (doc: any) => void;
  onInfoClick: (biomarker: any) => void;
  hideHeader?: boolean;
}

export const ExaminationPreview: React.FC<ExaminationPreviewProps> = ({
  selectedExam,
  examDocuments,
  onDocumentClick,
  onInfoClick,
  hideHeader = false,
}) => {
  const { t } = useTranslation();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<Record<string, Biomarker>>({});
  
  const { groupByCategory } = useBiomarkers({ 
    documents: examDocuments, 
    observations: selectedExam?.observations || [] 
  });
  
  const allBiomarkers = useMemo(() => {
    return Object.values(groupByCategory()).map((group: BiomarkerObservation[]) => group[0]);
  }, [groupByCategory]);

  const augmentedBiomarkers = useMemo(() => {
    return allBiomarkers.map(b => {
      if (b.slug && catalog[b.slug]) {
        return { ...b, info: catalog[b.slug].info };
      }
      return b;
    });
  }, [allBiomarkers, catalog]);

  useEffect(() => {
    biomarkerService.getAllBiomarkers().then(data => {
      const map: Record<string, Biomarker> = {};
      data.forEach(b => {
        map[b.slug] = b;
      });
      setCatalog(map);
    }).catch(console.error);
  }, []);

  if (!selectedExam) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-10 text-center opacity-30">
        <div className="w-20 h-20 bg-gray-100 dark:bg-dark-bg rounded-full flex items-center justify-center mb-6">
          <ClipboardList className="w-10 h-10" />
        </div>
        <p className="text-lg font-black uppercase tracking-widest">{t('common.select_to_view')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {!hideHeader && (
        <div className="p-4 px-6 border-b border-gray-100 dark:border-dark-border flex items-start justify-between bg-white dark:bg-dark-surface sticky top-0 z-10">
          <div className="flex items-start space-x-4 min-w-0">
            <div className="w-10 h-10 bg-blue-50 dark:bg-blue-900/30 rounded-xl flex items-center justify-center text-blue-600 dark:text-blue-400 flex-shrink-0 shadow-sm mt-0.5">
              <FileText className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h2 
                className="text-xl font-bold text-gray-900 dark:text-dark-text hover:text-blue-600 cursor-pointer transition-all duration-300 transform hover:translate-x-1 flex items-center group truncate"
                onClick={() => navigate(`/examinations/${selectedExam.id}`)}
                title={t('common.details')}
              >
                <span className="relative">
                  {selectedExam.organization?.name 
                    ? selectedExam.organization.name
                    : selectedExam.doctors?.length > 0 
                      ? `${t('doctors.dr')} ${selectedExam.doctors[0].name}${selectedExam.doctors.length > 1 ? ' +' : ''}` 
                      : t('examinations.clinical_examination')
                  }
                  <span className="absolute -bottom-0.5 left-0 w-0 h-0.5 bg-blue-600 transition-all duration-300 group-hover:w-full opacity-50"></span>
                </span>
                <ArrowRight className="w-5 h-5 ml-2 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all duration-300 text-blue-600 flex-shrink-0" />
              </h2>
              <div className="flex flex-wrap items-center gap-3 mt-1 text-xs text-gray-500 dark:text-dark-muted">
                <div className="flex items-center space-x-1.5 bg-gray-50 dark:bg-dark-bg px-2 py-0.5 rounded-md">
                  <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                  <span className="font-medium">{new Date(selectedExam.examination_date).toLocaleDateString()}</span>
                </div>
                {selectedExam.doctors?.length > 0 && (
                  <div className="flex items-center space-x-1.5 bg-blue-50/50 dark:bg-blue-900/20 px-2 py-0.5 rounded-md max-w-xs truncate">
                    <svg className="w-3.5 h-3.5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg>
                    <span className="font-semibold text-blue-600 dark:text-blue-400 truncate">
                      {selectedExam.doctors.map((d: any) => `${t('examinations.doctor')} ${d.name}`).join(', ')}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center space-x-2 flex-shrink-0">
            <ExaminationAIActions examinationId={selectedExam.id} />
            <button 
              onClick={() => navigate(`/examinations/${selectedExam.id}`)}
              className="p-1.5 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-xl transition-colors border border-transparent hover:border-blue-100 dark:hover:border-blue-900/30"
              title={t('common.details')}
            >
              <ExternalLink className="w-5 h-5" />
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto min-h-0 p-5 space-y-5 custom-scrollbar">
          {selectedExam && selectedExam.extraction_status && selectedExam.extraction_status !== 'completed' && (
            <div className="mb-4">
              <TaskProgressIndicator 
                examinationStatus={selectedExam.extraction_status}
                examinationProgress={selectedExam.extraction_progress}
                errorMessage={selectedExam.error_message}
                documents={examDocuments}
              />
            </div>
          )}

          <AssociatedEvents examinationId={selectedExam.id} patientId={selectedExam.patient_id} />
        
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {selectedExam.notes && stripHtml(selectedExam.notes) && (
              <div className="space-y-3">
                <div className="flex items-center space-x-2 text-gray-900 dark:text-dark-text">
                  <ClipboardList className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-bold uppercase tracking-wider">{t('examinations.examination_notes')}</h3>
                </div>
                <div className="bg-gray-50/50 dark:bg-dark-bg/30 border border-gray-100 dark:border-dark-border rounded-2xl p-5">
                  <div 
                    className="text-gray-700 dark:text-dark-text leading-relaxed prose prose-sm max-w-none prose-blue dark:prose-invert"
                    dangerouslySetInnerHTML={{ __html: selectedExam.notes }}
                  />
                </div>
              </div>
            )}

            {selectedExam.patient_notes && stripHtml(selectedExam.patient_notes) && (
              <div className="space-y-3">
                <div className="flex items-center space-x-2 text-gray-900 dark:text-dark-text">
                  <User className="w-4 h-4 text-green-500" />
                  <h3 className="text-sm font-bold uppercase tracking-wider">{t('examinations.personal_notes')}</h3>
                </div>
                <div className="bg-green-50/30 dark:bg-green-900/10 border border-green-100/50 dark:border-green-900/30 rounded-2xl p-5">
                  <div 
                    className="text-gray-700 dark:text-dark-text leading-relaxed prose prose-sm max-w-none prose-green dark:prose-invert"
                    dangerouslySetInnerHTML={{ __html: selectedExam.patient_notes }}
                  />
                </div>
              </div>
            )}
          </div>

          {(selectedExam.diagnoses?.length > 0 || selectedExam.impressions || selectedExam.medications?.length > 0) && (
            <div className="flex flex-col space-y-4">
              {selectedExam.impressions && (
                <div className="bg-white dark:bg-dark-surface/40 p-6 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-2">
                      <BriefcaseMedical className="w-4 h-4 text-blue-500" />
                      <span className="text-[10px] font-black uppercase tracking-wider text-gray-900 dark:text-dark-text">{t('examinations.clinical_impression')}</span>
                    </div>
                    <AIBadge />
                  </div>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-dark-text leading-relaxed">
                      <ReactMarkdown>{selectedExam.impressions}</ReactMarkdown>
                  </div>
                </div>
              )}

              {selectedExam.diagnoses?.length > 0 && (
                <div className="bg-white dark:bg-dark-surface/40 p-6 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-2">
                      <Bookmark className="w-4 h-4 text-blue-500" />
                      <span className="text-[10px] font-black uppercase tracking-wider text-gray-900 dark:text-dark-text">{t('examinations.extracted_diagnoses')}</span>
                    </div>
                    <AIBadge />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedExam.diagnoses.map((d: string) => (
                      <span key={d} className="px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded-xl text-xs font-bold border border-blue-100 dark:border-blue-800/30 shadow-sm">{d}</span>
                    ))}
                  </div>
                </div>
              )}

              {selectedExam.medications?.length > 0 && (
                <div className="bg-white dark:bg-dark-surface/40 p-6 rounded-[2rem] border border-gray-100 dark:border-dark-border shadow-sm">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center space-x-2">
                      <Pill className="w-4 h-4 text-indigo-500" />
                      <span className="text-[10px] font-black uppercase tracking-wider text-gray-900 dark:text-dark-text">{t('examinations.identified_medications')}</span>
                    </div>
                    <AIBadge />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {selectedExam.medications.map((m: any, idx: number) => (
                      <div key={idx} className="bg-gray-50/50 dark:bg-dark-bg/30 p-4 rounded-xl border border-gray-100 dark:border-dark-border shadow-sm group relative">
                        <div className="flex justify-between items-start">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-black text-gray-900 dark:text-dark-text truncate">{m.code?.text}</p>
                            <div className="flex flex-wrap gap-x-3 mt-1 opacity-70">
                              {m.dosage && <span className="text-[10px] font-bold text-indigo-600 dark:text-indigo-400 uppercase">{m.dosage}</span>}
                              {m.frequency?.display && <span className="text-[10px] font-bold text-gray-500 uppercase">{m.frequency.display}</span>}
                            </div>
                          </div>
                          {m.code?.catalog_id && (
                            <div className="flex items-center space-x-1">
                              <MedicationAIActions 
                                medicationId={m.code.catalog_id} 
                                medicationName={m.code.text}
                              />
                              <button 
                                onClick={(e) => { e.stopPropagation(); navigate(`/medications/details/${m.code.catalog_id}`); }}
                                className="p-1.5 text-indigo-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                              >
                                <ExternalLink className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          )}
                        </div>
                        {m.reason && (
                          <p className="text-[9px] text-indigo-500 font-bold mt-2 uppercase flex items-center">
                            <Stethoscope className="w-3 h-3 mr-1" />
                            {m.reason}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

        {allBiomarkers.length > 0 && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2 text-gray-900 dark:text-dark-text">
                <Activity className="w-4 h-4 text-red-500" />
                <h3 className="text-sm font-bold uppercase tracking-wider">{t('examinations.key_biomarkers')}</h3>
              </div>
            </div>
            <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl overflow-hidden shadow-sm">
              <div className="max-h-64 overflow-y-auto custom-scrollbar">
                <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border relative">
                  <thead className="bg-gray-50 dark:bg-dark-bg sticky top-0 z-10 shadow-sm">
                    <tr>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('common.biomarkers')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('examinations.table.result')}</th>
                      <th className="px-6 py-3 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('examinations.table.range')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                    {augmentedBiomarkers.map((b) => (
                    <tr key={b.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg transition-colors group/row">
                      <td className="px-6 py-4 text-sm font-bold text-gray-900 dark:text-dark-text flex items-center">
                        {b.definitionId ? (
                          <Link to={`/biomarkers/details/${b.definitionId}`} className="hover:text-blue-600 transition-colors">
                            {b.displayName}
                          </Link>
                        ) : (
                          <span>{b.displayName}</span>
                        )}
                        {b.info && (
                          <button 
                            onClick={() => onInfoClick(b)}
                            className="ml-2 p-1 text-blue-400 opacity-0 group-hover/row:opacity-100 transition-opacity hover:text-blue-600"
                            title={t('common.details')}
                          >
                            <Info className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        <span className="font-bold text-blue-600 dark:text-blue-400">{formatBiomarkerValue(b.value.raw, precisionProfile)}</span>
                        <span className="ml-1 text-xs text-gray-400 dark:text-dark-muted">{b.unit.rawSymbol}</span>
                        {isAbnormal(b.interpretation) && (
                          <span className="ml-2 text-[10px] font-bold text-red-500 uppercase">({b.interpretation})</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-xs text-gray-400 dark:text-dark-muted font-medium">{b.referenceRange.displayText}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          </div>
        )}

        {examDocuments.length > 0 && (
          <div className="space-y-4">
            {examDocuments.filter(d => d.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i)).length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center space-x-2 text-gray-900 dark:text-dark-text">
                  <ImageIcon className="w-4 h-4 text-purple-500" />
                  <h3 className="text-sm font-bold uppercase tracking-wider">{t('examinations.medical_imaging')}</h3>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                  {examDocuments.filter(d => d.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i)).map((doc) => (
                    <div key={doc.id} className="group relative aspect-square bg-gray-100 dark:bg-dark-bg rounded-xl overflow-hidden cursor-pointer border-2 border-transparent hover:border-blue-500 transition-all shadow-sm" onClick={() => onDocumentClick(doc)}>
                      <AuthenticatedThumbnail documentId={doc.id} filename={doc.filename} className="transition-transform duration-500 group-hover:scale-110" />
                      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 bg-black/20 z-20 transition-opacity">
                         <Search className="w-6 h-6 text-white" />
                      </div>
                      <div className="absolute bottom-0 left-0 right-0 p-2 bg-gradient-to-t from-black/60 to-transparent z-10">
                        <p className="text-[10px] text-white font-medium truncate">{doc.filename}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {examDocuments.filter(d => !d.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i)).length > 0 && (
              <div className="space-y-3">
                <div className="flex items-center space-x-2 text-gray-900 dark:text-dark-text">
                  <Download className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-bold uppercase tracking-wider">{t('examinations.attachments_reports')}</h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {examDocuments.filter(d => !d.filename.match(/\.(png|jpe?g|webp|gif|bmp|dcm)$/i)).map((doc) => (
                    <div key={doc.id} className="flex items-center p-4 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl hover:border-blue-200 dark:hover:border-blue-900 hover:bg-blue-50/30 dark:hover:bg-blue-900/20 transition-all cursor-pointer group shadow-sm" onClick={() => onDocumentClick(doc)}>
                      <div className="w-10 h-10 bg-blue-50 dark:bg-blue-900/30 text-blue-500 dark:text-blue-400 rounded-xl flex items-center justify-center flex-shrink-0 mr-3 group-hover:bg-blue-100 dark:group-hover:bg-blue-900/50 transition-colors">
                        <FileText className="w-5 h-5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-bold text-gray-900 dark:text-dark-text truncate">{doc.filename}</p>
                        <p className="text-[10px] text-gray-400 dark:text-dark-muted font-bold uppercase tracking-tighter">{doc.entities?.document_category || 'Document'}</p>
                      </div>
                      <ExternalLink className="w-4 h-4 text-gray-300 dark:text-dark-border group-hover:text-blue-500 dark:group-hover:text-blue-400 transition-colors" />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="p-4 px-6 border-t border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/50 flex items-center justify-between">
        <div className="flex flex-col">
          <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">{t('examinations.metadata')}</p>
          <p className="text-xs text-gray-500 dark:text-dark-muted">{t('examinations.recorded')}: {new Date(selectedExam.created_at).toLocaleString()}</p>
        </div>
        <div className="flex items-center space-x-3">
        </div>
      </div>
    </div>
  );
};
