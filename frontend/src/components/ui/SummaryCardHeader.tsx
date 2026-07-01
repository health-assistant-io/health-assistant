import React from 'react';
import { Plus, ExternalLink, type LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import OpenPageButton from './OpenPageButton';
import { InfoTooltip } from './InfoTooltip';

/**
 * Optional info popover configuration for the card title.
 * Renders an `i` icon next to the title that opens a click popover.
 */
export interface CardHeaderInfo {
  /** Popover heading (rendered in bold uppercase). */
  title?: string;
  /** Popover body — usually a short paragraph describing what the card shows. */
  content: React.ReactNode;
  /** Accessible label for the trigger icon. */
  ariaLabel?: string;
}

/**
 * Shared header for the patient-detail summary cards.
 *
 * Layout (responds to overflow with flex-wrap):
 *   ┌──────────────────────────────────────────┐
 *   │ [Icon] Title  ⓘ                     ↗   │
 *   │   [tag1] [tag2] [tag3]            [+ Add]│
 *   └──────────────────────────────────────────┘
 *
 * - Left column: icon + title (top, wraps to multiple lines if long), tags wrapped below.
 *   An optional ⓘ icon opens a click popover with explanatory content.
 * - Right column: OpenPageButton on top, Add button below (vertically stacked).
 *
 * Use the exported TAG_* class strings on `<span>`s passed via the `tags` prop
 * so every card uses identical badge styling.
 */

export interface SummaryCardHeaderProps {
  icon: LucideIcon;
  iconClassName?: string;
  title: string;
  /** Badge-style spans (use TAG_NEUTRAL / TAG_BLUE / etc.). */
  tags?: React.ReactNode[];
  /** Optional info popover configuration. */
  info?: CardHeaderInfo;
  /** If provided, the card title becomes clickable (in-app nav) with a hover "open in new tab" affordance. */
  titleTo?: string;
  /** If provided, renders the OpenPageButton (top-right). */
  onOpen?: () => void;
  openLabel?: string;
  /** If provided, renders the Add button (below the OpenPageButton). */
  onAdd?: () => void;
  addLabel?: string;
}

const SummaryCardHeader: React.FC<SummaryCardHeaderProps> = ({
  icon: Icon,
  iconClassName = 'text-blue-500',
  title,
  tags = [],
  info,
  titleTo,
  onOpen,
  openLabel,
  onAdd,
  addLabel,
}) => {
  const navigate = useNavigate();
  const hasActions = onOpen || onAdd;

  // Split the title so the LAST word + info icon share a `whitespace-nowrap` span.
  // This prevents the icon from being orphaned on its own line when the title
  // nearly fills the column. For multi-word titles, the icon glues to the final
  // word; for single-word titles, the whole word + icon is one nowrap unit.
  const renderTitleWithIcon = () => {
    if (!info) {
      return <span>{title}</span>;
    }
    const words = title.trim().split(/\s+/);
    if (words.length === 1) {
      return (
        <span className="whitespace-nowrap">
          {title}
          <InfoTooltip
            trigger="click"
            title={info.title}
            content={info.content}
            ariaLabel={info.ariaLabel}
            className="align-middle ml-1"
          />
        </span>
      );
    }
    const lastWord = words[words.length - 1];
    const before = words.slice(0, -1).join(' ');
    return (
      <>
        <span>{before} </span>
        <span className="whitespace-nowrap">
          {lastWord}
          <InfoTooltip
            trigger="click"
            title={info.title}
            content={info.content}
            ariaLabel={info.ariaLabel}
            className="align-middle ml-1"
          />
        </span>
      </>
    );
  };

  return (
    <div className="px-4 sm:px-6 py-4 border-b border-gray-50 dark:border-dark-border bg-white dark:bg-dark-surface">
      <div className="flex items-start justify-between gap-3">
        {/* Left: icon + title (wraps inline), tags wrapped below */}
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <Icon className={`w-5 h-5 ${iconClassName} shrink-0 mt-0.5`} />
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold text-gray-900 dark:text-dark-text leading-tight">
              <span
                className={titleTo ? 'group/title inline-flex items-center gap-1.5 cursor-pointer hover:opacity-80 transition-opacity' : ''}
                onClick={titleTo ? (e) => { e.stopPropagation(); navigate(titleTo); } : undefined}
                title={titleTo ? title : undefined}
              >
                {renderTitleWithIcon()}
                {titleTo && (
                  <ExternalLink
                    onClick={(e) => { e.stopPropagation(); e.preventDefault(); window.open(titleTo, '_blank', 'noopener,noreferrer'); }}
                    className="w-4 h-4 text-gray-400 dark:text-dark-muted opacity-0 group-hover/title:opacity-100 hover:!text-blue-500 transition-opacity shrink-0"
                  />
                )}
              </span>
            </h2>
            {tags.length > 0 && (
              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                {tags.map((tag, i) => (
                  <React.Fragment key={i}>{tag}</React.Fragment>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: actions stacked vertically (open on top, add below) */}
        {hasActions && (
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            {onOpen && <OpenPageButton onClick={onOpen} label={openLabel || ''} />}
            {onAdd && (
              <button
                type="button"
                onClick={onAdd}
                className="flex items-center justify-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all text-xs font-bold"
              >
                <Plus className="w-3 h-3" />
                <span>{addLabel}</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------- Shared tag badge class strings ----------
// Use these on <span> elements passed to the `tags` prop for consistent styling.

export const TAG_NEUTRAL =
  'px-2 py-0.5 bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted rounded-full text-[10px] font-bold uppercase shrink-0';

export const TAG_BLUE =
  'px-2 py-0.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-full text-[10px] font-bold uppercase shrink-0 border border-blue-100 dark:border-blue-900/30';

export const TAG_PURPLE =
  'px-2 py-0.5 bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 rounded-full text-[10px] font-bold uppercase shrink-0 border border-purple-100 dark:border-purple-900/30';

export const TAG_EMERALD =
  'px-2 py-0.5 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400 rounded-full text-[10px] font-bold uppercase shrink-0 border border-emerald-100 dark:border-emerald-900/30';

export const TAG_RED =
  'px-2 py-0.5 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-full text-[10px] font-bold uppercase shrink-0 border border-red-100 dark:border-red-900/30 animate-pulse';

export default SummaryCardHeader;
