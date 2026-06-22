import type { ForwardRefExoticComponent, RefAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  Image as ImageIcon,
  FileText,
  Plus,
  Calendar,
  ShieldAlert,
  Gauge,
  LayoutGrid,
  GitCompareArrows,
  Siren,
} from 'lucide-react';

import { BiomarkerCard } from './cards/BiomarkerCard';
import { TrendsCard } from './cards/TrendsCard';
import { ImageViewerCard } from './cards/ImageViewerCard';
import { ExaminationCard } from './cards/ExaminationCard';
import { BiomarkersCard } from './cards/BiomarkersCard';
import { UnifiedHealthCalendarCard } from './cards/UnifiedHealthCalendarCard';
import { AllergyAlertsCard } from './cards/AllergyAlertsCard';
import { LatestDocumentsCard } from './cards/LatestDocumentsCard';
import { RangeGaugeCard } from './cards/RangeGaugeCard';
import { HealthSummaryCard } from './cards/HealthSummaryCard';
import { MultiBiomarkerComparisonCard } from './cards/MultiBiomarkerComparisonCard';
import { AnomalyCard } from './cards/AnomalyCard';
import { getBestIcon } from './shared/icons';

export type CardCategory = 'biomarker' | 'documents' | 'clinical' | 'analytics';

export interface CardLayoutSize {
  /** default width (in grid columns) on large breakpoint */
  w: number;
  /** default height (in grid rows) on large breakpoint */
  h: number;
  minW?: number;
  minH?: number;
}

export interface CardDefaultConfigContext {
  /** first available biomarker slug, for biomarker-driven cards */
  defaultBiomarker: string;
  /** human label matching defaultBiomarker */
  biomarkerLabel: string;
}

export interface CardDefinition {
  /** canonical type key stored in cards_config */
  type: string;
  /** the React component to render */
  component: ForwardRefExoticComponent<any> & RefAttributes<any>;
  /** i18n key for the add-card menu label */
  labelKey: string;
  /** icon shown in the add-card menu */
  icon: LucideIcon;
  /** optional tailwind classes for the menu icon (color/rotation) */
  iconClassName?: string;
  /** default grid footprint when added */
  defaultLayout: CardLayoutSize;
  /** static default config; overridden by resolveDefaultConfig when present */
  defaultConfig: Record<string, any>;
  /** grouping for filtering / future categorization in the menu */
  category: CardCategory;
  /** whether this card consumes biomarker trends data */
  usesBiomarkers?: boolean;
  /** legacy type keys that should resolve to this card (e.g. medication_calendar -> health_calendar) */
  aliases?: string[];
  /** dynamic config factory for cards needing runtime defaults (e.g. first biomarker) */
  resolveDefaultConfig?: (ctx: CardDefaultConfigContext) => Record<string, any>;
}

export const CARD_REGISTRY: CardDefinition[] = [
  {
    type: 'biomarker',
    component: BiomarkerCard,
    labelKey: 'dashboard.cards.biomarker_stats',
    icon: Activity,
    defaultLayout: { w: 3, h: 2, minW: 2, minH: 2 },
    defaultConfig: { biomarker: 'glucose', icon: 'Activity' },
    category: 'biomarker',
    usesBiomarkers: true,
    resolveDefaultConfig: ({ defaultBiomarker, biomarkerLabel }) => ({
      biomarker: defaultBiomarker,
      icon: getBestIcon(biomarkerLabel),
    }),
  },
  {
    type: 'trends',
    component: TrendsCard,
    labelKey: 'dashboard.cards.trend_graph',
    icon: Activity,
    iconClassName: 'rotate-90',
    defaultLayout: { w: 8, h: 4, minW: 4, minH: 3 },
    defaultConfig: { biomarker: 'glucose' },
    category: 'biomarker',
    usesBiomarkers: true,
    resolveDefaultConfig: ({ defaultBiomarker }) => ({ biomarker: defaultBiomarker }),
  },
  {
    type: 'labs',
    component: BiomarkersCard,
    labelKey: 'dashboard.cards.lab_results',
    icon: Plus,
    defaultLayout: { w: 12, h: 5, minW: 4, minH: 3 },
    defaultConfig: {},
    category: 'biomarker',
    usesBiomarkers: true,
  },
  {
    type: 'range_gauge',
    component: RangeGaugeCard,
    labelKey: 'dashboard.cards.range_gauge',
    icon: Gauge,
    defaultLayout: { w: 3, h: 3, minW: 2, minH: 2 },
    defaultConfig: { biomarker: 'glucose' },
    category: 'biomarker',
    usesBiomarkers: true,
    resolveDefaultConfig: ({ defaultBiomarker }) => ({ biomarker: defaultBiomarker }),
  },
  {
    type: 'multi_biomarker_comparison',
    component: MultiBiomarkerComparisonCard,
    labelKey: 'dashboard.cards.multi_biomarker_comparison',
    icon: GitCompareArrows,
    defaultLayout: { w: 8, h: 5, minW: 5, minH: 3 },
    defaultConfig: { biomarkers: [] },
    category: 'analytics',
    usesBiomarkers: true,
  },
  {
    type: 'health_summary',
    component: HealthSummaryCard,
    labelKey: 'dashboard.cards.health_summary',
    icon: LayoutGrid,
    defaultLayout: { w: 12, h: 2, minW: 4, minH: 2 },
    defaultConfig: {},
    category: 'analytics',
    usesBiomarkers: true,
  },
  {
    type: 'anomaly_alerts',
    component: AnomalyCard,
    labelKey: 'dashboard.cards.anomaly_alerts',
    icon: Siren,
    iconClassName: 'text-amber-500',
    defaultLayout: { w: 6, h: 4, minW: 3, minH: 3 },
    defaultConfig: {},
    category: 'analytics',
  },
  {
    type: 'imaging',
    component: ImageViewerCard,
    labelKey: 'dashboard.cards.image_viewer',
    icon: ImageIcon,
    iconClassName: 'text-blue-500',
    defaultLayout: { w: 4, h: 5, minW: 3, minH: 3 },
    defaultConfig: {},
    category: 'documents',
  },
  {
    type: 'latest_documents',
    component: LatestDocumentsCard,
    labelKey: 'dashboard.cards.latest_documents',
    icon: FileText,
    iconClassName: 'text-indigo-500',
    defaultLayout: { w: 4, h: 3, minW: 3, minH: 2 },
    defaultConfig: { viewMode: 'list' },
    category: 'documents',
  },
  {
    type: 'examination',
    component: ExaminationCard,
    labelKey: 'dashboard.cards.examination_note',
    icon: Plus,
    defaultLayout: { w: 4, h: 2, minW: 3, minH: 2 },
    defaultConfig: {},
    category: 'clinical',
  },
  {
    type: 'health_calendar',
    component: UnifiedHealthCalendarCard,
    labelKey: 'dashboard.cards.health_timeline',
    icon: Calendar,
    iconClassName: 'text-emerald-500',
    aliases: ['medication_calendar'],
    defaultLayout: { w: 6, h: 5, minW: 4, minH: 3 },
    defaultConfig: {
      viewType: 'timeline',
      timelineDays: 3,
      categories: ['medications', 'allergies', 'examinations'],
    },
    category: 'clinical',
  },
  {
    type: 'allergy_alerts',
    component: AllergyAlertsCard,
    labelKey: 'dashboard.cards.clinical_alerts',
    icon: ShieldAlert,
    iconClassName: 'text-red-500',
    defaultLayout: { w: 4, h: 3, minW: 3, minH: 2 },
    defaultConfig: {},
    category: 'clinical',
  },
];

const TYPE_INDEX: Record<string, CardDefinition> = (() => {
  const idx: Record<string, CardDefinition> = {};
  for (const def of CARD_REGISTRY) {
    idx[def.type] = def;
    for (const alias of def.aliases ?? []) idx[alias] = def;
  }
  return idx;
})();

export function getCardDefinition(type: string): CardDefinition | undefined {
  return TYPE_INDEX[type];
}

export function resolveCardComponent(type: string): ForwardRefExoticComponent<any> | undefined {
  return TYPE_INDEX[type]?.component;
}

export function resolveDefaultConfig(
  type: string,
  ctx: CardDefaultConfigContext
): Record<string, any> {
  const def = TYPE_INDEX[type];
  if (!def) return {};
  return def.resolveDefaultConfig ? def.resolveDefaultConfig(ctx) : def.defaultConfig;
}

export function resolveDefaultLayout(type: string): CardLayoutSize {
  const def = TYPE_INDEX[type];
  return def?.defaultLayout ?? { w: 4, h: 3 };
}

export const ADDABLE_CARDS: CardDefinition[] = CARD_REGISTRY;
