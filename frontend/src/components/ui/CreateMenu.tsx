import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Plus,
  FileText,
  Pill,
  Activity,
  ShieldAlert,
  User,
  Stethoscope,
  Building2,
  ChevronUp,
  type LucideIcon,
} from 'lucide-react';
import { useAuthStore } from '../../store/slices/authSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import type { Patient } from '../../types/patient';

export interface CreateMenuItem {
  id: string;
  labelKey: string;
  icon: LucideIcon;
  /** Navigate to this path on click (used if `onSelect`/`toBuilder` are not provided). */
  to?: string;
  /** Dynamic navigate target — resolved at click time. Useful for injecting currentPatient.id. */
  toBuilder?: (ctx: { currentPatient: Patient | null }) => string | null;
  /** Custom click handler — overrides navigation. */
  onSelect?: () => void;
  /** If true, the item is disabled when no `currentPatient` is set. */
  requiresPatient?: boolean;
  /** Restrict visibility to these roles. */
  roles?: string[];
  /** Optional grouping key (renders a divider + label). */
  category?: string;
}

export interface CreateMenuProps {
  /** Override the default item list. */
  items?: CreateMenuItem[];
  /** Icon-only mode (sidebar collapsed). */
  collapsed?: boolean;
  /** Override the trigger button label. */
  labelKey?: string;
  /** Extra classes on the trigger wrapper. */
  className?: string;
  /** Intercept every item click (overrides both `to` and `onSelect`). */
  onItemSelect?: (item: CreateMenuItem) => void;
  /** Visual style. */
  variant?: 'primary' | 'ghost';
}

/**
 * Default create actions surfaced in the sidebar / patient detail.
 *
 * URL convention: each item routes to its destination with `?new=<id>`, which
 * the target page picks up via `useCreateIntent` to auto-open its create modal.
 *
 * The allergy item has no dedicated list page — it routes to the current
 * patient's detail page, where `AllergySummary` listens for `?new=allergy`.
 */
export const DEFAULT_CREATE_ITEMS: CreateMenuItem[] = [
  {
    id: 'examination',
    labelKey: 'common.new_examination',
    icon: FileText,
    to: '/examinations/upload',
    category: 'clinical',
  },
  {
    id: 'medication',
    labelKey: 'common.new_medication',
    icon: Pill,
    to: '/medications?new=medication',
    requiresPatient: true,
    category: 'clinical',
  },
  {
    id: 'event',
    labelKey: 'common.new_clinical_event',
    icon: Activity,
    to: '/events?new=event',
    requiresPatient: true,
    category: 'clinical',
  },
  {
    id: 'allergy',
    labelKey: 'common.new_allergy',
    icon: ShieldAlert,
    toBuilder: ({ currentPatient }) =>
      currentPatient ? `/patients/${currentPatient.id}?new=allergy` : '/patients',
    requiresPatient: true,
    category: 'clinical',
  },
  {
    id: 'patient',
    labelKey: 'common.new_patient',
    icon: User,
    to: '/patients?new=patient',
    category: 'demographics',
  },
  {
    id: 'doctor',
    labelKey: 'common.new_doctor',
    icon: Stethoscope,
    to: '/doctors?new=doctor',
    category: 'demographics',
  },
  {
    id: 'organization',
    labelKey: 'common.new_organization',
    icon: Building2,
    to: '/organizations?new=organization',
    category: 'demographics',
  },
];

/** Category i18n keys + ordering. */
const CATEGORY_LABELS: Record<string, string> = {
  clinical: 'common.clinical_section',
  demographics: 'common.demographics_section',
  admin: 'common.administration',
  other: 'common.other',
};
const CATEGORY_ORDER = ['clinical', 'demographics', 'admin', 'other'];

const CreateMenu: React.FC<CreateMenuProps> = ({
  items,
  collapsed = false,
  labelKey = 'common.create_new',
  className = '',
  onItemSelect,
  variant = 'primary',
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore(state => state.user);
  const currentPatient = usePatientStore(state => state.currentPatient);

  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [triggerRect, setTriggerRect] = useState<DOMRect | null>(null);

  // Close on outside click or Escape
  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  // Recompute trigger position when opening (and track resize while open)
  useEffect(() => {
    if (!open) return;
    const measure = () => {
      if (triggerRef.current) setTriggerRect(triggerRef.current.getBoundingClientRect());
    };
    measure();
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [open]);

  // Close on route change (covers in-app navigation)
  const closeMenu = useCallback(() => setOpen(false), []);

  const visibleItems = useMemo(() => {
    const source = items ?? DEFAULT_CREATE_ITEMS;
    return source.filter(item => {
      if (item.roles && user && !item.roles.includes(user.role)) return false;
      return true;
    });
  }, [items, user]);

  const groupedItems = useMemo(() => {
    const groups: { category: string; items: CreateMenuItem[] }[] = [];
    const byCat = new Map<string, CreateMenuItem[]>();
    visibleItems.forEach(item => {
      const cat = item.category ?? 'other';
      if (!byCat.has(cat)) byCat.set(cat, []);
      byCat.get(cat)!.push(item);
    });
    CATEGORY_ORDER.forEach(cat => {
      const list = byCat.get(cat);
      if (list && list.length > 0) groups.push({ category: cat, items: list });
    });
    return groups;
  }, [visibleItems]);

  const hasInteractiveItems = visibleItems.length > 0;

  const handleItemClick = (item: CreateMenuItem) => {
    closeMenu();
    if (onItemSelect) {
      onItemSelect(item);
      return;
    }
    if (item.onSelect) {
      item.onSelect();
      return;
    }
    if (item.toBuilder) {
      const target = item.toBuilder({ currentPatient });
      if (target) navigate(target);
      return;
    }
    if (item.to) {
      navigate(item.to);
    }
  };

  const isItemDisabled = (item: CreateMenuItem): boolean =>
    !!item.requiresPatient && !currentPatient;

  // ---------- Trigger button ----------
  const triggerBase =
    variant === 'primary'
      ? 'bg-[#0088CC] hover:bg-[#0077B3] text-white shadow-md shadow-blue-100 dark:shadow-none'
      : 'bg-gray-50 dark:bg-dark-bg hover:bg-gray-100 dark:hover:bg-dark-border text-gray-700 dark:text-dark-text';

  const triggerShape = collapsed
    ? 'h-12 w-12 mx-auto rounded-xl flex items-center justify-center'
    : 'w-full px-4 py-3 rounded-xl flex items-center justify-center font-bold';

  const triggerContent = collapsed ? (
    <Plus className="w-5 h-5" />
  ) : (
    <>
      <Plus className="w-5 h-5 mr-2 shrink-0" />
      <span className="truncate">{t(labelKey)}</span>
      <ChevronUp className={`w-4 h-4 ml-2 shrink-0 transition-transform duration-200 ${open ? '' : 'rotate-180'}`} />
    </>
  );

  // ---------- Dropdown positioning ----------
  // When collapsed, fly out to the right of the trigger.
  // When expanded, anchor to the trigger's left edge and open upward.
  const dropdownStyle: React.CSSProperties = triggerRect
    ? collapsed
      ? {
          position: 'fixed',
          top: Math.max(8, triggerRect.bottom - 320),
          left: triggerRect.right + 12,
          minWidth: 240,
          maxWidth: 280,
        }
      : {
          position: 'fixed',
          bottom: window.innerHeight - triggerRect.top + 8,
          left: triggerRect.left,
          width: Math.max(triggerRect.width, 240),
        }
    : { display: 'none' };

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(prev => !prev)}
        title={collapsed ? t(labelKey) : ''}
        className={`${triggerBase} ${triggerShape} transition-all active:scale-95 ${open ? 'ring-2 ring-blue-300/50' : ''}`}
      >
        {triggerContent}
      </button>

      {open && hasInteractiveItems && triggerRect &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={dropdownStyle}
            className="z-[1000] bg-white dark:bg-dark-surface rounded-2xl shadow-[0_8px_30px_-4px_rgba(0,0,0,0.15)] dark:shadow-[0_8px_30px_-4px_rgba(0,0,0,0.6)] border border-gray-100 dark:border-dark-border py-2 animate-in fade-in zoom-in-95 duration-150 ring-1 ring-black/5 dark:ring-white/5 max-h-[60vh] overflow-y-auto custom-scrollbar"
          >
            {collapsed && (
              <div className="px-4 py-2 border-b border-gray-100 dark:border-dark-border mb-1">
                <span className="text-xs font-bold text-gray-500 dark:text-dark-muted uppercase tracking-wider">
                  {t(labelKey)}
                </span>
              </div>
            )}
            {groupedItems.map((group, gi) => (
              <div key={group.category}>
                {gi > 0 && <div className="h-px bg-gray-50 dark:bg-dark-border my-1 mx-2" />}
                <div className="px-4 pt-2 pb-1 text-[9px] font-black text-gray-300 dark:text-dark-border uppercase tracking-widest">
                  {t(CATEGORY_LABELS[group.category] || group.category)}
                </div>
                {group.items.map(item => {
                  const Icon = item.icon;
                  const disabled = isItemDisabled(item);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="menuitem"
                      disabled={disabled}
                      onClick={() => handleItemClick(item)}
                      className={`w-full flex items-center px-4 py-2 text-sm rounded-lg transition-colors mx-0 ${
                        disabled
                          ? 'text-gray-300 dark:text-dark-border cursor-not-allowed'
                          : 'text-gray-600 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-bg hover:text-gray-900 dark:hover:text-dark-text'
                      }`}
                      title={disabled ? t('common.select_patient_first') : undefined}
                    >
                      <Icon className={`w-4 h-4 mr-3 shrink-0 ${disabled ? 'text-gray-300 dark:text-dark-border' : 'text-gray-400 dark:text-dark-muted'}`} />
                      <span className="truncate text-left">{t(item.labelKey)}</span>
                      {disabled && (
                        <span className="ml-auto text-[9px] font-black uppercase tracking-widest text-gray-300 dark:text-dark-border">
                          {t('common.requires_patient')}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
};

export default CreateMenu;
