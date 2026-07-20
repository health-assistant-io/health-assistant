import React from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity, Baby, AlertTriangle, Zap, Scissors, Smile, Eye, Sparkles, CheckCircle, Stethoscope,
} from 'lucide-react';
import type { ClinicalEventType } from '../../services/clinicalEventService';

/**
 * Phase 8j: shared icon resolver for clinical-event categories + types.
 *
 * Extracted from `ClinicalEventForm.tsx` so other surfaces (EventList,
 * AIToolsModal, dashboard cards, etc.) can reuse the same icon mapping
 * without duplicating the switch. Type icons render at `w-5 h-5`
 * (the historical default for the form's type cards); category icons
 * at `w-4 h-4` (their historical default). Callers wrapping the icon
 * in their own sized container (e.g. `p-3 rounded-2xl`) get the right
 * visual either way.
 *
 * Adding a new type/category icon = add a `case` here. Slugs without
 * a case fall back to `Activity` (the historical default).
 */
export function getEventIcon(slug: string) {
  switch (slug) {
    // Type slugs → larger icons.
    case 'pain-episode': return <Activity className="w-5 h-5" />;
    case 'pregnancy': return <Baby className="w-5 h-5" />;
    case 'accident': return <AlertTriangle className="w-5 h-5" />;
    case 'flare-up': return <Zap className="w-5 h-5" />;
    case 'surgical-recovery': return <Scissors className="w-5 h-5" />;
    case 'dental': return <Smile className="w-5 h-5" />;
    case 'vision': return <Eye className="w-5 h-5" />;
    case 'aesthetic': return <Sparkles className="w-5 h-5" />;
    case 'maintenance': return <CheckCircle className="w-5 h-5" />;
    // Category slugs → smaller icons.
    case 'reproductive-health': return <Baby className="w-4 h-4" />;
    case 'acute-chronic': return <Activity className="w-4 h-4" />;
    case 'specialized-care': return <Stethoscope className="w-4 h-4" />;
    case 'routine-wellness': return <CheckCircle className="w-4 h-4" />;
    case 'general-event': return <Activity className="w-4 h-4" />;
    default: return <Activity className="w-5 h-5" />;
  }
}

interface EventTypeCardProps {
  type: ClinicalEventType;
  isSelected: boolean;
  onSelect: (type: ClinicalEventType) => void;
  /**
   * When true, shows the parent category name below the description. Used in
   * search results where types from multiple categories are mixed — gives
   * the user context for a type they searched for. Defaults to false
   * (drilldown mode — category is already implied).
   */
  showCategoryHint?: boolean;
  /**
   * When true (default), shows the localized description below the name,
   * clamped to 2 lines. Set to false for compact layouts (e.g. inline
   * pickers where hover-tooltip is enough).
   */
  showDescription?: boolean;
}

/**
 * Phase 8j: shared card for picking a `ClinicalEventType` in a grid.
 *
 * Used by the Record New Clinical Event form's picker (both the drilldown
 * view and the search-results view). Extracted from `ClinicalEventForm.tsx`
 * so other surfaces (EventList, AIToolsModal, future dashboards) can render
 * the same card without duplicating the markup + i18n lookup + selection
 * styling.
 *
 * The card is a `<button>` (not a div) — it's a single interactive unit with
 * no nested buttons, so semantic HTML is clean. Accessibility: reachable by
 * Tab, activates on Enter/Space (default button behavior).
 *
 * Localization: name + description go through `t()` with the backend string
 * as fallback, matching the Phase 8i pattern. Custom tenant-created types
 * (slugs not in the i18n files) render their raw backend strings.
 */
export const EventTypeCard: React.FC<EventTypeCardProps> = ({
  type,
  isSelected,
  onSelect,
  showCategoryHint = false,
  showDescription = true,
}) => {
  const { t } = useTranslation();
  const description = showDescription
    ? t(`events.type.${type.slug}.description`, type.description || '')
    : '';

  return (
    <button
      type="button"
      onClick={() => onSelect(type)}
      aria-pressed={isSelected}
      className={`flex flex-col items-center justify-center p-4 rounded-2xl border transition-all duration-200 space-y-2 relative group text-center min-w-0 ${
        isSelected
          ? 'bg-white dark:bg-dark-surface border-blue-500 shadow-lg shadow-blue-500/10 ring-2 ring-blue-500/20'
          : 'bg-gray-50 dark:bg-dark-bg border-transparent hover:border-blue-200 hover:bg-white dark:hover:bg-dark-surface'
      }`}
    >
      <div
        className={`p-2.5 rounded-xl transition-all duration-200 ${
          isSelected
            ? 'bg-blue-600 text-white'
            : 'bg-white dark:bg-dark-surface shadow-sm text-gray-400 group-hover:text-blue-500'
        }`}
        style={isSelected ? undefined : { color: type.color }}
      >
        {getEventIcon(type.slug)}
      </div>
      <span
        className={`text-[10px] font-black uppercase tracking-tight leading-tight ${
          isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-dark-muted'
        }`}
      >
        {t(`events.type.${type.slug}.name`, type.name)}
      </span>
      {description && (
        <p className="text-[9px] text-gray-400 dark:text-dark-muted leading-snug line-clamp-2">
          {description}
        </p>
      )}
      {showCategoryHint && type.category_concept && (
        <span className="text-[9px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest">
          {t(`events.category.${type.category_concept.slug}.name`, type.category_concept.name)}
        </span>
      )}
      {isSelected && (
        <div className="absolute top-1.5 right-1.5">
          <CheckCircle className="w-3.5 h-3.5 text-blue-500" />
        </div>
      )}
    </button>
  );
};

export default EventTypeCard;
