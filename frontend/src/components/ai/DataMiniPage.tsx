import React from 'react';
import { Calendar, ExternalLink, Activity, Pill, ClipboardList, Info } from 'lucide-react';
import { format } from 'date-fns';
import { useNavigate } from 'react-router-dom';

interface DataMiniPageProps {
  data: any;
  toolName: string;
  onClose?: () => void;
}

export const DataMiniPage: React.FC<DataMiniPageProps> = ({ data, toolName, onClose }) => {
  const navigate = useNavigate();
  if (!data) return <p className="text-gray-400 italic text-xs p-4">No data available for this reference.</p>;

  const isArray = Array.isArray(data);
  const items = isArray ? data : [data];

  if (items.length === 0) return <p className="text-gray-400 italic text-xs p-4">Empty data set returned.</p>;

  // Detect data type
  const isObservation = toolName === 'observation' || (items[0] && (items[0].resourceType === 'Observation' || 'effective_datetime' in items[0] || 'value_quantity' in items[0]));
  const isBiomarkerDef = toolName === 'biomarker' || (items[0] && 'slug' in items[0] && ('info' in items[0] || 'description' in items[0]));
  const isMedication = toolName.includes('medication') || (items[0] && ('drug_name' in items[0] || items[0].resourceType === 'Medication'));
  const isExamination = toolName.includes('examination') || (items[0] && 'examination_date' in items[0]);
  const isEvent = toolName === 'event' || (items[0] && ('onset_date' in items[0] || 'occurrences' in items[0]));
  const isDocument = toolName.includes('document') || (items[0] && ('filename' in items[0] && 'extracted_text' in items[0]));
  const isTelemetryData = !isObservation && !isBiomarkerDef && !isMedication && !isExamination && !isEvent && !isDocument && items[0] && 'value' in items[0] && 'date' in items[0];

  const handleNavigate = (type: string, id: string, extraData?: any) => {
    if (onClose) onClose();
    
    if (type === 'observation' && extraData?.examination_id) {
      navigate(`/examinations/${extraData.examination_id}/biomarkers?highlight=${id}`);
    } else if (type === 'biomarker') {
      navigate(`/biomarkers/details/${id}`);
    } else if (type === 'medication') {
      navigate(`/medications/details/${id}`);
    } else if (type === 'examination') {
      navigate(`/examinations/${id}`);
    } else if (type === 'event') {
      navigate(`/events/${id}`);
    } else if (type === 'document') {
      navigate(`/documents/${id}`);
    }
  };

  return (
    <div className="space-y-3 p-1 w-full text-left">
      {items.map((item, idx) => {
        // Extract common fields
        const name = item.code?.text || item.code?.coding?.[0]?.display || item.displayName || item.name || item.drug_name || item.category || 'Unknown Record';
        const date = item.effective_datetime || item.examination_date || item.source?.date || item.created_at;
        
        let id = item.id;
        // Ensure id is just the UUID even if a full FHIR URL was provided
        if (id && (id.includes('http') || id.includes('/'))) {
          const parts = id.split('/');
          id = parts[parts.length - 1];
        }
        
        const extractValue = (val: any) => {
          if (val === null || val === undefined) return 'N/A';
          if (typeof val === 'object') return val.parsedValue ?? val.source ?? JSON.stringify(val);
          return val;
        };

        const displayValue = extractValue(item.value_quantity?.value ?? item.value?.raw ?? item.value ?? item.value_string);
        const displayUnit = item.value_quantity?.unit ?? item.raw_unit?.symbol ?? item.unit?.rawSymbol ?? item.unit ?? '';
        
        let rangeText = 'N/A';
        if (item.lab_reference_range) {
          rangeText = `${extractValue(item.lab_reference_range.min)} - ${extractValue(item.lab_reference_range.max)}`;
        } else if (item.referenceRange) {
          rangeText = item.referenceRange.displayText || `${item.referenceRange.min || ''} - ${item.referenceRange.max || ''}`;
        }

        return (
          <div key={idx} className="bg-white dark:bg-dark-bg/40 rounded-xl border border-gray-100 dark:border-white/5 p-2 sm:p-3 shadow-sm group/item hover:border-indigo-200 dark:hover:border-indigo-900/50 transition-all">
            {isObservation && (
              <div className="space-y-1.5 sm:space-y-2">
                <div className="flex justify-between items-start gap-2">
                  <button 
                    onClick={() => handleNavigate('observation', id, item)}
                    className="flex-1 text-left group/title min-w-0"
                    title={`View observation details for ${name}`}
                  >
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5 truncate">
                      <Activity className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{name}</span>
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                  </button>
                  <span className={`px-1 sm:px-1.5 py-0.5 rounded text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0 ${
                    item.interpretation?.toLowerCase() === 'normal' || !item.interpretation
                      ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400'
                      : 'bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400'
                  }`}>
                    {item.interpretation || 'Observation'}
                  </span>
                </div>
                
                <div className="grid grid-cols-2 gap-1 sm:gap-2 mt-0.5 sm:mt-1">
                  <div className="flex flex-col">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Value</span>
                    <span className="text-xs font-mono font-bold text-indigo-600 dark:text-indigo-400 leading-none">
                      {displayValue} <span className="text-[8px] sm:text-[9px] ml-0.5 opacity-70 font-normal">{displayUnit}</span>
                    </span>
                  </div>
                  <div className="flex flex-col">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Reference</span>
                    <span className="text-[9px] sm:text-[10px] font-medium text-gray-600 dark:text-dark-text truncate leading-none">
                      {rangeText}
                    </span>
                  </div>
                </div>
                
                {date && (
                  <div className="flex items-center gap-1 sm:gap-1.5 pt-1 sm:pt-1.5 border-t border-gray-50 dark:border-white/5 opacity-60">
                     <Calendar className="w-2 sm:w-2.5 h-2 sm:h-2.5" />
                     <span className="text-[8px] sm:text-[9px] font-medium">{format(new Date(date), 'MMM d, yyyy')}</span>
                  </div>
                )}
              </div>
            )}

            {isBiomarkerDef && (
              <div className="space-y-1.5 sm:space-y-2">
                <button 
                  onClick={() => handleNavigate('biomarker', id)}
                  className="w-full text-left group/title"
                  title={`View biomarker definition for ${item.name}`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5">
                      <Info className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      {item.name}
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                    <div className="flex flex-col items-end gap-1">
                      <span className="px-1 sm:px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400 text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0">
                        Definition
                      </span>
                      {item.is_telemetry && (
                        <span className="px-1 sm:px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400 text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0 flex items-center gap-1">
                          <Activity className="w-2 h-2" />
                          Telemetry
                        </span>
                      )}
                    </div>
                  </div>
                </button>
                <div className="flex flex-col mt-0.5 sm:mt-1">
                  <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest mb-0.5">Description</span>
                  <p className="text-[9px] sm:text-[10px] leading-relaxed text-gray-700 dark:text-dark-text line-clamp-3 sm:line-clamp-4">
                    {item.info || item.description || 'No clinical description available.'}
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1.5 sm:pt-2 border-t border-gray-50 dark:border-white/5">
                   <div className="flex flex-col">
                      <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 font-bold leading-none mb-1">Category</span>
                      <span className="text-[8px] sm:text-[9px] font-medium text-indigo-600 uppercase leading-none">{item.category || 'General'}</span>
                   </div>
                   <div className="flex flex-col text-right">
                      <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 font-bold leading-none mb-1">Standard Unit</span>
                      <span className="text-[8px] sm:text-[9px] font-medium font-mono leading-none">{item.preferred_unit_symbol || item.unit || 'N/A'}</span>
                   </div>
                </div>
              </div>
            )}

            {isMedication && (
              <div className="space-y-1.5 sm:space-y-2">
                <button 
                  onClick={() => handleNavigate('medication', id)}
                  className="w-full text-left group/title"
                  title={`View medication details for ${name}`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5 truncate">
                      <Pill className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{name}</span>
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                    <span className="px-1 sm:px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400 text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0">
                      {item.status || 'Active'}
                    </span>
                  </div>
                </button>
                <div className="flex flex-col mt-0.5 sm:mt-1">
                  <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Instruction</span>
                  <span className="text-xs font-medium text-gray-700 dark:text-dark-text leading-tight">
                    {item.dosage || 'N/A'} {item.frequency ? `— ${item.frequency}` : ''}
                  </span>
                </div>
                {item.reason && (
                  <div className="flex flex-col mt-0.5 sm:mt-1">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Indication</span>
                    <span className="text-[9px] sm:text-[10px] leading-relaxed text-gray-600 dark:text-dark-muted italic">"{item.reason}"</span>
                  </div>
                )}
              </div>
            )}

            {isExamination && (
              <div className="space-y-1.5 sm:space-y-2">
                <button 
                  onClick={() => handleNavigate('examination', id)}
                  className="w-full text-left group/title"
                  title={`View examination details`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5 truncate">
                      <ClipboardList className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{item.category || 'Clinical Examination'}</span>
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                    <div className="flex items-center gap-1 text-[8px] sm:text-[9px] font-bold text-gray-500 dark:text-dark-muted shrink-0">
                      <Calendar className="w-2.5 h-2.5" />
                      {date ? format(new Date(date), 'MMM d, yyyy') : 'N/A'}
                    </div>
                  </div>
                </button>
                {(item.impressions || item.notes) && (
                  <div className="flex flex-col mt-0.5 sm:mt-1">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Findings</span>
                    <span className="text-[9px] sm:text-[10px] leading-relaxed text-gray-600 dark:text-dark-muted line-clamp-2 sm:line-clamp-3">
                      {item.impressions || item.notes}
                    </span>
                  </div>
                )}
                {item.doctor_name && (
                  <div className="pt-1 sm:pt-1.5 border-t border-gray-50 dark:border-white/5">
                     <span className="text-[8px] sm:text-[9px] font-bold text-indigo-600 dark:text-indigo-400 uppercase tracking-tighter">Dr. {item.doctor_name}</span>
                  </div>
                )}
              </div>
            )}

            {isEvent && (
              <div className="space-y-1.5 sm:space-y-2">
                <button 
                  onClick={() => handleNavigate('event', id)}
                  className="w-full text-left group/title"
                  title={`View details for ${item.title}`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5 truncate">
                      <Calendar className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{item.title}</span>
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                    <span className={`px-1 sm:px-1.5 py-0.5 rounded text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0 ${
                      item.status?.toLowerCase() === 'active'
                        ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400'
                        : 'bg-gray-50 text-gray-600 dark:bg-gray-500/10 dark:text-gray-400'
                    }`}>
                      {item.status || 'Event'}
                    </span>
                  </div>
                </button>
                <div className="flex flex-col mt-0.5 sm:mt-1">
                   <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 font-bold leading-none mb-1">Type</span>
                   <span className="text-[8px] sm:text-[9px] font-medium text-indigo-600 uppercase leading-none">{item.type_details?.name || item.type || 'Clinical Event'}</span>
                </div>
                {item.description && (
                  <div className="flex flex-col mt-0.5 sm:mt-1">
                    <p className="text-[9px] sm:text-[10px] leading-relaxed text-gray-600 dark:text-dark-muted line-clamp-2 italic">
                      "{item.description}"
                    </p>
                  </div>
                )}
                {item.onset_date && (
                  <div className="flex items-center gap-1 sm:gap-1.5 pt-1 sm:pt-1.5 border-t border-gray-50 dark:border-white/5 opacity-60">
                     <span className="text-[8px] sm:text-[9px] font-medium">Started: {format(new Date(item.onset_date), 'MMM d, yyyy')}</span>
                     {item.resolved_date && (
                       <>
                         <span className="text-[8px] opacity-40">—</span>
                         <span className="text-[8px] sm:text-[9px] font-medium">Ended: {format(new Date(item.resolved_date), 'MMM d, yyyy')}</span>
                       </>
                     )}
                  </div>
                )}
              </div>
            )}

            {isDocument && (
              <div className="space-y-1.5 sm:space-y-2">
                <button 
                  onClick={() => handleNavigate('document', id)}
                  className="w-full text-left group/title"
                  title={`View details for ${item.filename}`}
                >
                  <div className="flex justify-between items-start gap-2">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight group-hover/title:text-indigo-600 dark:group-hover/title:text-indigo-400 transition-colors flex items-center gap-1 sm:gap-1.5 truncate">
                      <ClipboardList className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{item.filename}</span>
                      <ExternalLink className="w-2 sm:w-2.5 h-2 sm:h-2.5 opacity-0 group-hover/title:opacity-100 transition-opacity shrink-0" />
                    </h4>
                    <span className="px-1 sm:px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400 text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0">
                      Document
                    </span>
                  </div>
                </button>
                <div className="flex flex-col mt-0.5 sm:mt-1">
                   <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 font-bold leading-none mb-1">Status</span>
                   <span className="text-[8px] sm:text-[9px] font-medium text-indigo-600 uppercase leading-none">{item.status || 'Processed'}</span>
                </div>
                {item.extracted_text && (
                  <div className="flex flex-col mt-0.5 sm:mt-1">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Content Preview</span>
                    <p className="text-[9px] sm:text-[10px] leading-relaxed text-gray-600 dark:text-dark-muted line-clamp-4 italic">
                      "{item.extracted_text}"
                    </p>
                  </div>
                )}
                {item.created_at && (
                  <div className="flex items-center gap-1 sm:gap-1.5 pt-1 sm:pt-1.5 border-t border-gray-50 dark:border-white/5 opacity-60">
                     <Calendar className="w-2 sm:w-2.5 h-2 sm:h-2.5" />
                     <span className="text-[8px] sm:text-[9px] font-medium">Uploaded: {format(new Date(item.created_at), 'MMM d, yyyy')}</span>
                  </div>
                )}
              </div>
            )}

            {isTelemetryData && (
              <div className="space-y-1.5 sm:space-y-2">
                <div className="flex justify-between items-start gap-2">
                  <div className="flex-1 text-left min-w-0">
                    <h4 className="text-[10px] sm:text-[11px] font-black uppercase text-gray-900 dark:text-white leading-tight flex items-center gap-1 sm:gap-1.5 truncate">
                      <Activity className="w-2.5 sm:w-3 h-2.5 sm:h-3 text-amber-500 shrink-0" />
                      <span className="truncate">{name !== 'Unknown Record' ? name : toolName.replace(/get_aggregated_|trends|_/g, ' ')}</span>
                    </h4>
                  </div>
                  <span className={`px-1 sm:px-1.5 py-0.5 rounded text-[7px] sm:text-[8px] font-black uppercase tracking-wider shrink-0 bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400`}>
                    Telemetry
                  </span>
                </div>
                
                <div className="grid grid-cols-2 gap-1 sm:gap-2 mt-0.5 sm:mt-1">
                  <div className="flex flex-col">
                    <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Average Value</span>
                    <span className="text-xs font-mono font-bold text-indigo-600 dark:text-indigo-400 leading-none">
                      {item.value} <span className="text-[8px] sm:text-[9px] ml-0.5 opacity-70 font-normal">{item.unit}</span>
                    </span>
                  </div>
                  {(item.min_value !== undefined || item.max_value !== undefined) && (
                    <div className="flex flex-col">
                      <span className="text-[7px] sm:text-[8px] uppercase text-gray-400 dark:text-dark-muted font-bold tracking-widest leading-none mb-1">Range (Min-Max)</span>
                      <span className="text-[9px] sm:text-[10px] font-medium text-gray-600 dark:text-dark-text truncate leading-none font-mono">
                        {item.min_value ?? '--'} - {item.max_value ?? '--'}
                      </span>
                    </div>
                  )}
                </div>
                
                {item.date && (
                  <div className="flex items-center gap-1 sm:gap-1.5 pt-1 sm:pt-1.5 border-t border-gray-50 dark:border-white/5 opacity-60">
                     <Calendar className="w-2 sm:w-2.5 h-2 sm:h-2.5" />
                     <span className="text-[8px] sm:text-[9px] font-medium">{format(new Date(item.date), 'MMM d, yyyy HH:mm')}</span>
                  </div>
                )}
              </div>
            )}

            {!isObservation && !isBiomarkerDef && !isMedication && !isExamination && !isEvent && !isDocument && !isTelemetryData && (
              <div className="space-y-1">
                {Object.entries(item).filter(([k]) => !k.startsWith('_')).slice(0, 5).map(([key, val]) => (
                  <div key={key} className="flex justify-between items-center py-1 border-b border-gray-50 dark:border-white/5 last:border-0">
                    <span className="text-[9px] uppercase font-bold text-gray-400 dark:text-dark-muted tracking-tight">{key.replace(/_/g, ' ')}</span>
                    <span className="text-[10px] font-medium text-gray-700 dark:text-dark-text truncate max-w-[120px]" title={String(val)}>
                      {typeof val === 'object' ? '...' : String(val)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {id && (
              <div className="mt-2 pt-1.5 border-t border-gray-50 dark:border-white/5 flex items-center justify-between opacity-50 hover:opacity-100 transition-opacity">
                <span className="text-[7px] font-black uppercase text-gray-400 tracking-widest">ID</span>
                <span className="text-[8px] font-mono text-gray-500 truncate max-w-[150px] select-all" title={id}>{id}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
