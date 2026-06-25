import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Database, Info, ExternalLink, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { DataMiniPage } from './DataMiniPage';
import { ToolCallInfo } from '../../types/ai';
import { getObservation } from '../../services/observationService';
import { getMedication } from '../../services/medicationService';
import { getExamination } from '../../services/examinationService';
import { getEvent } from '../../services/clinicalEventService';
import { getDocument } from '../../services/documentService';
import biomarkerService from '../../services/biomarkerService';

interface CitationButtonProps {
  reference: string; // format: "type=uuid" or "tool_name"
  toolCalls: ToolCallInfo[];
}

export const CitationButton: React.FC<CitationButtonProps> = ({ reference, toolCalls }) => {
  const [showPopup, setShowPopup] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fetchedData, setFetchedData] = useState<any>(null);
  const [popupPosition, setPopupPosition] = useState<{ top: number, left: number, placement: 'top' | 'bottom' }>({ top: 0, left: 0, placement: 'top' });
  const buttonRef = useRef<HTMLSpanElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const closeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();

  const isUUIDReference = reference.includes('=');
  let [type, uuid] = isUUIDReference 
    ? reference.split('=').map(s => s.trim()) 
    : [reference.trim(), null];

  // Handle case where UUID is actually a full URL (from LLM or user input)
  if (uuid && (uuid.includes('http') || uuid.includes('/'))) {
    const parts = uuid.split('/');
    uuid = parts[parts.length - 1];
  }

  // Handle truncated UUIDs from LLM (e.g. "uuid...")
  const isTruncated = uuid?.endsWith('...');
  const searchUuid = isTruncated ? uuid?.replace('...', '') : uuid;

  const handleMouseEnter = () => {
    if (closeTimeoutRef.current) {
      clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    setShowPopup(true);
    fetchData();
  };

  const handleMouseLeave = () => {
    // Add a small delay before closing to allow moving between button and popup
    // and provide a "perimeter" buffer effect
    closeTimeoutRef.current = setTimeout(() => {
      setShowPopup(false);
    }, 300);
  };

  // Find tool call for legacy fallback OR to resolve truncated UUIDs
  const toolCall = [...toolCalls].reverse().find(tc => {
    const nameMatch = tc.name.toLowerCase() === reference.toLowerCase();
    if (nameMatch) return true;
    
    // If we have a truncated UUID, try to find the full one in tool results
    if (isTruncated && tc.result) {
      return tc.result.includes(searchUuid || '');
    }
    return false;
  });

  const updatePosition = () => {
    if (buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      const spaceAbove = rect.top;
      const spaceBelow = window.innerHeight - rect.bottom;
      
      // Determine if we should show above or below based on available space
      // Popup height is roughly 350-400px at most
      const placement = (spaceAbove > 400 || spaceAbove > spaceBelow) ? 'top' : 'bottom';
      
      setPopupPosition({
        top: placement === 'top' ? rect.top - 12 : rect.bottom + 12,
        left: rect.left + (rect.width / 2),
        placement
      });
    }
  };

  useEffect(() => {
    if (showPopup) {
      // Small delay to ensure button position is stable if layout is shifting
      const timer = setTimeout(updatePosition, 0);
      // Listen for scroll events on any parent to reposition the fixed popup
      window.addEventListener('scroll', updatePosition, true);
      window.addEventListener('resize', updatePosition);
      return () => {
        clearTimeout(timer);
        window.removeEventListener('scroll', updatePosition, true);
        window.removeEventListener('resize', updatePosition);
      };
    }
  }, [showPopup]);

  const fetchData = async () => {
    if (fetchedData || loading) return;
    
    let finalUuid = searchUuid;

    // If truncated, try to resolve the full UUID from tool results
    if (isTruncated && toolCall?.result) {
      try {
        const results = JSON.parse(toolCall.result);
        const items = Array.isArray(results) ? results : [results];
        // Find item that starts with our partial UUID
        const fullItem = items.find(item => 
          (item.id && item.id.startsWith(searchUuid)) || 
          (item.biomarker_id && item.biomarker_id.startsWith(searchUuid))
        );
        if (fullItem) {
          finalUuid = type === 'biomarker' ? (fullItem.biomarker_id || fullItem.id) : fullItem.id;
        }
      } catch (e) {}
    }
    
    if (isUUIDReference && finalUuid && (!isTruncated || (isTruncated && finalUuid.length > (searchUuid?.length || 0)))) {
      setLoading(true);
      try {
        let data = null;
        if (type === 'observation') {
          data = await getObservation(finalUuid);
        } else if (type === 'biomarker') {
          data = await biomarkerService.getBiomarkerById(finalUuid);
        } else if (type === 'medication') {
          data = await getMedication(finalUuid);
        } else if (type === 'examination') {
          data = await getExamination(finalUuid);
        } else if (type === 'event') {
          data = await getEvent(finalUuid);
        } else if (type === 'document') {
          data = await getDocument(finalUuid);
        }
        setFetchedData(data);
      } catch (err) {
        console.error(`Failed to fetch citation data for ${reference}`, err);
      } finally {
        setLoading(false);
      }
    } else if (toolCall?.result) {
      // Legacy fallback: Use tool call result directly
      try {
        setFetchedData(JSON.parse(toolCall.result));
      } catch (e) {
        setFetchedData(toolCall.result);
      }
    }
  };

  const handleOpenOriginal = () => {
    // Determine the full UUID even if the initial reference was truncated
    let finalUuid = searchUuid;
    if (isTruncated && toolCall?.result) {
      try {
        const results = JSON.parse(toolCall.result);
        const items = Array.isArray(results) ? results : [results];
        const fullItem = items.find(item => 
          (item.id && item.id.startsWith(searchUuid)) || 
          (item.biomarker_id && item.biomarker_id.startsWith(searchUuid))
        );
        if (fullItem) {
          finalUuid = type === 'biomarker' ? (fullItem.biomarker_id || fullItem.id) : fullItem.id;
        }
      } catch (e) {}
    }

    if (isUUIDReference && finalUuid) {
      if (type === 'examination') {
        navigate(`/examinations/${finalUuid}`);
      } else if (type === 'observation') {
        // Navigate to examination's biomarker tab and highlight this observation
        if (fetchedData?.examination_id) {
          navigate(`/examinations/${fetchedData.examination_id}/biomarkers?highlight=${finalUuid}`);
        } else {
          navigate(`/biomarkers`);
        }
      } else if (type === 'biomarker') {
        navigate(`/biomarkers/details/${finalUuid}`);
      } else if (type === 'medication') {
        navigate(`/medications/details/${finalUuid}`);
      } else if (type === 'event') {
        navigate(`/events/${finalUuid}`);
      } else if (type === 'document') {
        navigate(`/documents/${finalUuid}`);
      }
    } else if (toolCall) {
      // Legacy navigation logic
      try {
        const args = typeof toolCall.args === 'string' ? JSON.parse(toolCall.args) : toolCall.args;
        if (toolCall.name === 'get_examination_details' && args.examination_id) {
          navigate(`/examinations/${args.examination_id}`);
        } else if (toolCall.name === 'get_biomarker_details' && args.biomarker_id) {
          navigate(`/biomarkers/details/${args.biomarker_id}`);
        } else if (toolCall.name === 'get_aggregated_biomarker_trends') {
          // Prefer the biomarker UUID from the tool result; fall back to slug only if absent
          let bioId: string | undefined = args.biomarker_id;
          if (!bioId && toolCall.result) {
            try {
              const results = JSON.parse(toolCall.result);
              const items = Array.isArray(results) ? results : [results];
              const found = items.find((it: any) => it && (it.biomarker_id || it.id));
              bioId = found?.biomarker_id || found?.id;
            } catch (e) {}
          }
          if (bioId) {
            navigate(`/biomarkers/details/${bioId}`);
          } else if (args.biomarker_slug) {
            navigate(`/biomarkers/details/${args.biomarker_slug}`);
          } else {
            navigate('/biomarkers');
          }
        } else if (toolCall.name.includes('biomarker')) {
          navigate('/biomarkers');
        } else if (toolCall.name.includes('medication')) {
          navigate('/medications');
        }
      } catch (e) {}
    }
    setShowPopup(false);
  };

  const getOriginUrl = () => {
    let finalUuid = searchUuid;
    if (isTruncated && toolCall?.result) {
      try {
        const results = JSON.parse(toolCall.result);
        const items = Array.isArray(results) ? results : [results];
        const fullItem = items.find(item => 
          (item.id && item.id.startsWith(searchUuid)) || 
          (item.biomarker_id && item.biomarker_id.startsWith(searchUuid))
        );
        if (fullItem) {
          finalUuid = type === 'biomarker' ? (fullItem.biomarker_id || fullItem.id) : fullItem.id;
        }
      } catch (e) {}
    }

    if (isUUIDReference && finalUuid) {
      if (type === 'examination') return `/examinations/${finalUuid}`;
      if (type === 'observation') {
        if (fetchedData?.examination_id) return `/examinations/${fetchedData.examination_id}/biomarkers?highlight=${finalUuid}`;
        return `/biomarkers`;
      }
      if (type === 'biomarker') return `/biomarkers/details/${finalUuid}`;
      if (type === 'medication') return `/medications/details/${finalUuid}`;
      if (type === 'event') return `/events/${finalUuid}`;
      if (type === 'document') return `/documents/${finalUuid}`;
    } else if (toolCall) {
      try {
        const args = typeof toolCall.args === 'string' ? JSON.parse(toolCall.args) : toolCall.args;
        if (toolCall.name === 'get_examination_details' && args.examination_id) return `/examinations/${args.examination_id}`;
        if (toolCall.name === 'get_biomarker_details' && args.biomarker_id) return `/biomarkers/details/${args.biomarker_id}`;
        if (toolCall.name === 'get_aggregated_biomarker_trends') {
          let bioId: string | undefined = args.biomarker_id;
          if (!bioId && toolCall.result) {
            try {
              const results = JSON.parse(toolCall.result);
              const items = Array.isArray(results) ? results : [results];
              const found = items.find((it: any) => it && (it.biomarker_id || it.id));
              bioId = found?.biomarker_id || found?.id;
            } catch (e) {}
          }
          if (bioId) return `/biomarkers/details/${bioId}`;
          if (args.biomarker_slug) return `/biomarkers/details/${args.biomarker_slug}`;
        }
        if (toolCall.name.includes('biomarker')) return '/biomarkers';
        if (toolCall.name.includes('medication')) return '/medications';
      } catch (e) {}
    }
    return '#';
  };

  const displayName = isUUIDReference 
    ? (uuid && uuid.length < 20 ? uuid.replace(/-/g, ' ') : type)
    : reference.replace(/get_|recent_|history|_details/g, '').replace(/_/g, ' ');

  if (!isUUIDReference && !toolCall) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-gray-50 dark:bg-dark-surface/50 text-gray-400 dark:text-dark-muted border border-gray-100 dark:border-white/5 text-[9px] font-black uppercase tracking-widest align-middle">
        <Info className="w-2.5 h-2.5" />
        {displayName}
      </span>
    );
  }

  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640;
  const popupWidth = isMobile ? window.innerWidth - 24 : 320;
  const halfWidth = popupWidth / 2;
  
  // Constrain center point (left) so edges are at least 12px from window borders
  const minLeft = halfWidth + 12;
  const maxLeft = (typeof window !== 'undefined' ? window.innerWidth : 1000) - halfWidth - 12;
  const calculatedLeft = Math.min(Math.max(popupPosition.left, minLeft), maxLeft);
  
  // Arrow offset relative to popup center
  // popupPosition.left is where the button center is.
  // calculatedLeft is where the popup center is.
  // We constrain the arrow offset so it doesn't go off the popup edges
  const arrowOffset = Math.min(Math.max(popupPosition.left - calculatedLeft, -halfWidth + 24), halfWidth - 24);

  return (
    <span ref={buttonRef} className="relative inline-block mx-0.5 align-middle">
      <span
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-800 text-[10px] font-black uppercase tracking-widest hover:bg-indigo-100 dark:hover:bg-indigo-800/50 transition-colors shadow-sm cursor-help"
      >
        <Database className="w-2.5 h-2.5" />
        {displayName}
      </span>

      {showPopup && createPortal(
        <div 
          ref={popupRef}
          className={`fixed z-[1000] w-[calc(100vw-24px)] sm:w-80 bg-white dark:bg-dark-surface rounded-2xl shadow-2xl border border-gray-100 dark:border-dark-border p-3 sm:p-4 animate-in fade-in zoom-in-95 duration-200 before:absolute before:-inset-4 before:z-[-1] before:content-['']`}
          style={{
            top: popupPosition.placement === 'top' ? 'auto' : `${popupPosition.top}px`,
            bottom: popupPosition.placement === 'top' ? `${window.innerHeight - popupPosition.top}px` : 'auto',
            left: `${calculatedLeft}px`,
            transform: 'translateX(-50%)'
          }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          {/* Arrow */}
          <div 
            className={`absolute left-1/2 border-8 border-transparent drop-shadow-sm ${
              popupPosition.placement === 'top' 
                ? 'top-full -mt-0.5 border-t-white dark:border-t-dark-surface' 
                : 'bottom-full -mb-0.5 border-b-white dark:border-b-dark-surface'
            }`} 
            style={{ 
              marginLeft: `${arrowOffset}px`,
              transform: 'translateX(-50%)'
            }}
          />
          
          <div className="flex items-center justify-between mb-2 sm:mb-3 pb-2 border-b border-gray-100 dark:border-white/5">
             <div className="flex items-center gap-1.5 sm:gap-2">
                <div className="p-1 sm:p-1.5 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 rounded-lg">
                   <Database className="w-3 sm:w-3.5 h-3 sm:h-3.5" />
                </div>
                <div>
                   <span className="text-[8px] sm:text-[10px] font-black uppercase tracking-widest text-gray-500 dark:text-dark-muted block leading-none mb-0.5">
                     {isUUIDReference ? 'Live Data' : 'Data Source'}
                   </span>
                   <span className="text-[8px] sm:text-[9px] font-mono text-indigo-600 dark:text-indigo-400 leading-none">{displayName}</span>
                </div>
             </div>
             <a 
               href={getOriginUrl()}
               onClick={(e) => {
                 e.preventDefault();
                 handleOpenOriginal();
               }}
               className="flex items-center gap-1 px-1.5 sm:px-2 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-[8px] sm:text-[9px] font-black uppercase tracking-widest transition-all shadow-md active:scale-95 whitespace-nowrap"
             >
                <ExternalLink className="w-2.5 sm:w-3 h-2.5 sm:h-3" />
                Origin
             </a>
          </div>

          <div className="max-h-64 sm:max-h-56 overflow-auto custom-scrollbar bg-gray-50/50 dark:bg-dark-bg/20 rounded-xl sm:rounded-2xl p-2 sm:p-3 border border-gray-100 dark:border-white/5 shadow-inner min-h-[80px] flex flex-col items-center justify-center">
             {loading ? (
               <div className="flex flex-col items-center gap-2 py-4">
                 <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
                 <span className="text-[10px] font-black uppercase text-gray-400 tracking-tighter">Fetching Clinical Record...</span>
               </div>
             ) : fetchedData ? (
               <DataMiniPage 
                  data={fetchedData} 
                  toolName={type} 
                  onClose={() => setShowPopup(false)}
               />
             ) : (
               <p className="text-[10px] text-gray-400 italic">No record found for this reference.</p>
             )}
          </div>
        </div>,
        document.body
      )}
    </span>
  );
};
