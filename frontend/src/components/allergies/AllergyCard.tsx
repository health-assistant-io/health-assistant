import React, { useState, useRef, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ShieldAlert, ExternalLink, Edit2, Trash2, MoreVertical, AlertTriangle, Clock } from 'lucide-react';
import { AllergyIntolerance } from '../../services/allergyService';
import { AllergyAIActions } from '../ui/AllergyAIActions';

export interface AllergyCardProps {
  allergy: AllergyIntolerance;
  showActions?: boolean;
  onEdit?: (a: AllergyIntolerance) => void;
  onDelete?: (a: AllergyIntolerance) => void;
  compact?: boolean;
}

const AllergyCard: React.FC<AllergyCardProps> = ({
  allergy,
  showActions = true,
  onEdit,
  onDelete,
  compact = false,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const isActive = (allergy.clinical_status ?? '').toUpperCase() === 'ACTIVE';
  const isHigh = (allergy.criticality ?? '').toUpperCase() === 'HIGH';

  const getStatusColor = (status?: string) => {
    switch ((status ?? '').toUpperCase()) {
      case 'ACTIVE':
        return 'bg-emerald-50 text-emerald-700 border-emerald-100';
      case 'RESOLVED':
        return 'bg-blue-50 text-blue-700 border-blue-100';
      case 'INACTIVE':
        return 'bg-gray-50 text-gray-600 border-gray-100';
      default:
        return 'bg-gray-50 text-gray-600 border-gray-100';
    }
  };

  const updateCoords = () => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setCoords({ top: rect.bottom, left: rect.left, width: rect.width });
    }
  };

  useEffect(() => {
    if (menuOpen) {
      updateCoords();
      window.addEventListener('scroll', updateCoords, true);
      window.addEventListener('resize', updateCoords);
    }
    return () => {
      window.removeEventListener('scroll', updateCoords, true);
      window.removeEventListener('resize', updateCoords);
    };
  }, [menuOpen]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(event.target as Node)
      ) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const actionMenu = useMemo(() => {
    if (!menuOpen) return null;
    return createPortal(
      <div
        ref={menuRef}
        className="fixed w-56 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-2xl z-[9999] py-2 animate-in zoom-in-95 duration-200"
        style={{
          top: `${coords.top + 8}px`,
          left: `${coords.left - 224 + coords.width}px`,
        }}
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-2 border-b border-gray-50 dark:border-dark-border mb-1">
          <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">
            {t('common.actions')}
          </p>
        </div>
        {allergy.code?.catalog_id && (
          <button
            onClick={() => {
              navigate(`/allergies/details/${allergy.code.catalog_id}`);
              setMenuOpen(false);
            }}
            className="w-full flex items-center space-x-3 px-4 py-2.5 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 text-gray-700 dark:text-dark-text transition-colors text-left"
          >
            <ExternalLink className="w-4 h-4 text-indigo-500" />
            <span className="text-xs font-bold">{t('common.view_details')}</span>
          </button>
        )}
        {onEdit && (
          <button
            onClick={() => {
              onEdit(allergy);
              setMenuOpen(false);
            }}
            className="w-full flex items-center space-x-3 px-4 py-2.5 hover:bg-blue-50 dark:hover:bg-blue-900/20 text-gray-700 dark:text-dark-text transition-colors text-left"
          >
            <Edit2 className="w-4 h-4 text-blue-500" />
            <span className="text-xs font-bold">{t('common.edit')}</span>
          </button>
        )}
        {onDelete && (
          <button
            onClick={() => {
              onDelete(allergy);
              setMenuOpen(false);
            }}
            className="w-full flex items-center space-x-3 px-4 py-2.5 hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 transition-colors text-left"
          >
            <Trash2 className="w-4 h-4" />
            <span className="text-xs font-bold">{t('common.delete')}</span>
          </button>
        )}
      </div>,
      document.body,
    );
  }, [menuOpen, coords, allergy, t, navigate, onEdit, onDelete]);

  return (
    <div
      className={`relative flex flex-col ${compact ? 'p-3 sm:p-4 max-w-sm min-h-[110px]' : 'p-4 sm:p-6 w-full min-h-[140px]'} bg-white dark:bg-dark-bg/60 border border-gray-100 dark:border-dark-border rounded-2xl group hover:border-rose-300 transition-all shadow-sm ${isHigh && isActive ? 'ring-1 ring-rose-200' : ''}`}
    >
      {showActions && (
        <div className="absolute top-3 right-3 flex items-center space-x-1">
          {allergy.code?.catalog_id && (
            <div className="opacity-0 group-hover:opacity-100 transition-opacity duration-200">
              <AllergyAIActions
                allergyId={allergy.code.catalog_id}
                allergyName={allergy.code.text}
              />
            </div>
          )}
          <button
            ref={triggerRef}
            onClick={e => {
              e.stopPropagation();
              setMenuOpen(!menuOpen);
            }}
            className={`p-2 rounded-xl transition-all ${menuOpen ? 'bg-rose-50 text-rose-600 shadow-inner' : 'text-gray-400 hover:bg-gray-50 hover:text-gray-600'}`}
            aria-label={t('common.actions')}
          >
            <MoreVertical className="w-5 h-5" />
          </button>
        </div>
      )}

      {actionMenu}

      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 sm:gap-4 pr-16">
        <div className="flex items-start space-x-3 sm:space-x-4 flex-1 min-w-0">
          <div
            className={`${compact ? 'p-2 sm:p-2.5' : 'p-2.5 sm:p-3'} rounded-xl shrink-0 ${isActive ? (isHigh ? 'bg-rose-50 dark:bg-rose-900/30 text-rose-600' : 'bg-amber-50 dark:bg-amber-900/30 text-amber-600') : 'bg-gray-50 dark:bg-dark-bg text-gray-400'} border border-transparent group-hover:border-rose-100 transition-colors`}
          >
            <ShieldAlert className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex flex-col">
              <div className="mb-1 sm:mb-2 flex items-center gap-1.5">
                <span
                  className={`px-1.5 py-0.5 rounded-full text-[7px] sm:text-[8px] font-black uppercase tracking-widest border shrink-0 ${getStatusColor(allergy.clinical_status)}`}
                >
                  {allergy.clinical_status}
                </span>
                {allergy.criticality && (
                  <span
                    className={`px-1.5 py-0.5 rounded-full text-[7px] sm:text-[8px] font-black uppercase tracking-widest border shrink-0 ${isHigh ? 'bg-rose-50 text-rose-700 border-rose-200' : 'bg-gray-50 text-gray-600 border-gray-200'}`}
                  >
                    {allergy.criticality.replace('_', ' ')}
                  </span>
                )}
              </div>

              <p
                onClick={() => allergy.code?.catalog_id && navigate(`/allergies/details/${allergy.code.catalog_id}`)}
                className={`${compact ? 'text-sm sm:text-base' : 'text-base sm:text-lg'} font-black text-gray-900 dark:text-dark-text transition-colors ${allergy.code?.catalog_id ? 'hover:text-rose-600 cursor-pointer' : ''}`}
              >
                {allergy.code?.text}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-x-2 sm:gap-x-3 gap-y-1 mt-2">
              {allergy.category && (
                <span className="text-[9px] sm:text-[10px] font-black text-gray-500 dark:text-dark-muted uppercase flex items-center">
                  <AlertTriangle className="w-2.5 h-2.5 sm:w-3 sm:h-3 mr-1 opacity-50 shrink-0" />
                  {allergy.category}
                </span>
              )}
              {allergy.onset_date && (
                <span className="text-[9px] sm:text-[10px] font-black text-gray-500 dark:text-dark-muted uppercase flex items-center">
                  <Clock className="w-2.5 h-2.5 sm:w-3 sm:h-3 mr-1 opacity-50 shrink-0" />
                  {new Date(allergy.onset_date).toLocaleDateString()}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {allergy.reactions && allergy.reactions.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-50 dark:border-dark-border">
          <div className="flex flex-wrap gap-1.5">
            {allergy.reactions.map((r, i) => (
              <span
                key={i}
                className={`px-2 py-1 rounded-lg text-[9px] sm:text-[10px] font-bold uppercase border ${r.severity === 'SEVERE' ? 'bg-rose-50 text-rose-700 border-rose-100 dark:bg-rose-900/20 dark:text-rose-400' : r.severity === 'MODERATE' ? 'bg-amber-50 text-amber-700 border-amber-100 dark:bg-amber-900/20 dark:text-amber-400' : 'bg-blue-50 text-blue-700 border-blue-100 dark:bg-blue-900/20 dark:text-blue-400'}`}
              >
                {r.manifestation}
              </span>
            ))}
          </div>
        </div>
      )}

      {allergy.note && (
        <div className="mt-3 flex items-start space-x-2">
          <AlertTriangle className="w-3.5 h-3.5 text-gray-400 mt-0.5 shrink-0" />
          <p className="text-[11px] text-gray-500 dark:text-dark-muted leading-relaxed italic line-clamp-2">
            {allergy.note}
          </p>
        </div>
      )}

      {allergy.resolved_date && (
        <div className="mt-4 flex items-center justify-between text-[9px] font-black text-gray-400 uppercase tracking-[0.1em]">
          <span>
            {t('allergies.resolved', 'Resolved')}: {new Date(allergy.resolved_date).toLocaleDateString()}
          </span>
        </div>
      )}
    </div>
  );
};

export default AllergyCard;
