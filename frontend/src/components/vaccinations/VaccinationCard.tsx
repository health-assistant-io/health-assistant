/**
 * VaccinationCard — presentational card for a patient immunization record.
 *
 * Mirrors `MedicationCard`'s shape (icon tile, status badge, action menu) but
 * stays lean by delegating status styling to the shared `vaccinationStatus`
 * helper. The card never calls the API — all mutations flow through the
 * parent via `onEdit` / `onDelete`.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  Syringe,
  MoreVertical,
  Edit2,
  Trash2,
  ExternalLink,
  Calendar,
  Hash,
  Building2,
  Package,
} from 'lucide-react';
import type { PatientImmunization } from '../../types/vaccine';
import { getStatusMeta } from './vaccinationStatus';

export interface VaccinationCardProps {
  immunization: PatientImmunization;
  onEdit?: (imm: PatientImmunization) => void;
  onDelete?: (imm: PatientImmunization) => void;
  compact?: boolean;
}

export const VaccinationCard: React.FC<VaccinationCardProps> = ({
  immunization,
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

  const status = getStatusMeta(immunization.status);
  const dateLabel = immunization.administered_at || immunization.created_at;

  useEffect(() => {
    if (!menuOpen) return;
    const update = () => {
      if (triggerRef.current) {
        const rect = triggerRef.current.getBoundingClientRect();
        setCoords({ top: rect.bottom, left: rect.left, width: rect.width });
      }
    };
    update();
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [menuOpen]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (
        menuRef.current?.contains(e.target as Node) ||
        triggerRef.current?.contains(e.target as Node)
      )
        return;
      setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const meta = useMemo(() => {
    const items: { icon: React.ReactNode; text: string }[] = [];
    if (immunization.dose_number)
      items.push({
        icon: <Hash className="w-2.5 h-2.5 sm:w-3 sm:h-3 opacity-50" />,
        text: `${t('vaccinations.dose')} ${immunization.dose_number}`,
      });
    if (immunization.lot_number)
      items.push({
        icon: <Package className="w-2.5 h-2.5 sm:w-3 sm:h-3 opacity-50" />,
        text: `${t('vaccinations.lot')} ${immunization.lot_number}`,
      });
    if (immunization.manufacturer)
      items.push({
        icon: <Building2 className="w-2.5 h-2.5 sm:w-3 sm:h-3 opacity-50" />,
        text: immunization.manufacturer,
      });
    return items;
  }, [immunization, t]);

  const actionMenu = useMemo(() => {
    if (!menuOpen) return null;
    return createPortal(
      <div
        ref={menuRef}
        style={{
          top: `${coords.top + 8}px`,
          left: `${coords.left - 224 + coords.width}px`,
        }}
        className="fixed w-56 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-2xl z-[9999] py-2 animate-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-2 border-b border-gray-50 dark:border-dark-border mb-1">
          <p className="text-[10px] font-black uppercase tracking-widest text-gray-400">
            {t('common.actions')}
          </p>
        </div>

        {immunization.vaccine_catalog_id && (
          <button
            onClick={() => {
              navigate('/catalogs?type=vaccine');
              setMenuOpen(false);
            }}
            className="w-full flex items-center space-x-3 px-4 py-2.5 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 text-gray-700 dark:text-dark-text transition-colors text-left"
          >
            <ExternalLink className="w-4 h-4 text-indigo-500" />
            <span className="text-xs font-bold">
              {t('vaccinations.open_in_catalog')}
            </span>
          </button>
        )}

        {onEdit && (
          <button
            onClick={() => {
              onEdit(immunization);
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
              onDelete(immunization);
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
  }, [menuOpen, coords, immunization, t, navigate, onEdit, onDelete]);

  return (
    <div
      className={`relative flex flex-col ${
        compact ? 'p-3 sm:p-4 max-w-sm min-h-[110px]' : 'p-4 sm:p-6 w-full min-h-[140px]'
      } bg-white dark:bg-dark-bg/60 border border-gray-100 dark:border-dark-border rounded-2xl group hover:border-indigo-300 transition-all shadow-sm`}
    >
      <div className="absolute top-3 right-3">
        <button
          ref={triggerRef}
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
          className={`p-2 rounded-xl transition-all ${
            menuOpen
              ? 'bg-indigo-50 text-indigo-600 shadow-inner'
              : 'text-gray-400 hover:bg-gray-50 hover:text-gray-600'
          }`}
        >
          <MoreVertical className="w-5 h-5" />
        </button>
      </div>
      {actionMenu}

      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 sm:gap-4 pr-12">
        <div className="flex items-start space-x-3 sm:space-x-4 flex-1 min-w-0">
          <div
            className={`${
              compact ? 'p-2 sm:p-2.5' : 'p-2.5 sm:p-3'
            } rounded-xl shrink-0 border border-transparent ${status.tileClass}`}
          >
            <Syringe className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="mb-1 sm:mb-2">
              <span
                className={`px-1.5 py-0.5 rounded-full text-[7px] sm:text-[8px] font-black uppercase tracking-widest border shrink-0 ${status.badgeClass}`}
              >
                {immunization.status.replace(/-/g, ' ')}
              </span>
            </div>

            {(immunization.administered_at || immunization.created_at) && (
              <p className="text-[7px] sm:text-[8px] font-bold text-gray-400 uppercase tracking-widest mb-1 flex items-center">
                <Calendar className="w-2.5 h-2.5 mr-1 opacity-50" />
                {new Date(dateLabel as string).toLocaleDateString()}
              </p>
            )}

            <p className={`${compact ? 'text-sm sm:text-base' : 'text-base sm:text-lg'} font-black text-gray-900 dark:text-dark-text break-words`}>
              {immunization.vaccine_code?.text}
            </p>

            {meta.length > 0 && (
              <div className="flex flex-wrap items-center gap-x-2 sm:gap-x-3 gap-y-1 mt-2">
                {meta.map((m, i) => (
                  <span
                    key={i}
                    className="text-[9px] sm:text-[10px] font-black text-gray-500 dark:text-dark-muted uppercase flex items-center"
                  >
                    {m.icon}
                    <span className="ml-1">{m.text}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {(immunization.location || immunization.note) && (
        <div className="mt-4 pt-4 border-t border-gray-50 dark:border-dark-border space-y-2">
          {immunization.location && (
            <p className="text-[10px] text-indigo-600 dark:text-indigo-400 font-black uppercase italic tracking-tighter flex items-center">
              <Building2 className="w-3.5 h-3.5 mr-1.5" />
              {immunization.location}
            </p>
          )}
          {immunization.note && (
            <p className="text-[11px] text-gray-500 dark:text-dark-muted leading-relaxed italic">
              {immunization.note}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default VaccinationCard;
