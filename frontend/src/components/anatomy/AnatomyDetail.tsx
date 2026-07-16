import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Layers,
  Box,
  Heart,
  Zap,
  Tag,
  FileText,
  ChevronRight,
  ChevronDown,
  Network,
  BookOpen,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AnatomyStructure, AnatomyRelatedResponse } from '../../types/anatomy';
import { CATEGORY_COLORS } from '../../types/anatomy';
import { OrganPreview } from './OrganPreview';
import { markerForStructure, useAnatomyAtlas } from './atlas';

interface Props {
  structure: AnatomyStructure;
  related: AnatomyRelatedResponse | null;
  onSelectRelated: (structure: AnatomyStructure) => void;
  /** Opens the relationship graph modal. */
  onViewGraph: () => void;
}

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  SYSTEM: <Layers className="w-5 h-5" />,
  REGION: <Box className="w-5 h-5" />,
  ORGAN: <Heart className="w-5 h-5" />,
  ORGAN_PART: <Heart className="w-5 h-5" />,
  TISSUE: <Zap className="w-5 h-5" />,
  CELL: <Zap className="w-5 h-5" />,
  SUBSTANCE: <Zap className="w-5 h-5" />,
  JOINT: <Box className="w-5 h-5" />,
  OTHER: <Tag className="w-5 h-5" />,
};

export const AnatomyDetail: React.FC<Props> = ({
  structure,
  related,
  onSelectRelated,
  onViewGraph,
}) => {
  const { t } = useTranslation();
  const figureOrder = useAnatomyAtlas((s) => s.figureOrder);
  const parents = related?.incoming ?? [];
  const children = related?.outgoing ?? [];
  const parentCount = parents.length;
  const childCount = children.length;
  const categoryColor = CATEGORY_COLORS[structure.category];
  const { figureSlug: markerFigure, marker } = markerForStructure(structure, figureOrder);

  const [showParents, setShowParents] = useState(false);
  const [showChildren, setShowChildren] = useState(false);

  const renderRelatedList = (
    items: NonNullable<AnatomyRelatedResponse['incoming']>,
    titleKey: string
  ) => (
    <div>
      <p className="text-[10px] font-black uppercase text-gray-400 tracking-widest mb-2">
        {t(titleKey)}
      </p>
      <div className="space-y-1.5">
        {items.map((rel, idx) => (
          <button
            key={idx}
            onClick={() => onSelectRelated(rel.structure)}
            className="w-full flex items-center justify-between px-3 py-2 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl text-sm hover:border-blue-300 dark:hover:border-blue-700 transition-colors group"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: CATEGORY_COLORS[rel.structure.category] }}
              />
              <span className="font-medium text-gray-700 dark:text-dark-text truncate">
                {rel.structure.name}
              </span>
              <span className="text-[9px] text-gray-400 uppercase flex-shrink-0">
                {t(`anatomy.relations.${rel.relation_type}`)}
              </span>
            </div>
            <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-blue-400 transition-colors flex-shrink-0" />
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <div
        className="rounded-2xl p-5 border-2"
        style={{ borderColor: categoryColor, background: `${categoryColor}10` }}
      >
        <div className="flex items-start gap-3">
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center text-white flex-shrink-0"
            style={{ background: categoryColor }}
          >
            {CATEGORY_ICONS[structure.category]}
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text truncate">
              {structure.name}
            </h3>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span
                className="text-[10px] font-black uppercase tracking-wider px-2 py-0.5 rounded-full text-white"
                style={{ background: categoryColor }}
              >
                {t(`anatomy.categories.${structure.category}`)}
              </span>
              {structure.standard_code && (
                <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 dark:bg-dark-bg text-gray-500 dark:text-dark-muted uppercase">
                  {structure.standard_system}: {structure.standard_code}
                </span>
              )}
              {structure.is_custom && (
                <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400">
                  {t('anatomy.custom_badge')}
                </span>
              )}
            </div>
          </div>
        </div>

        {structure.description && (
          <div className="mt-3 flex items-start gap-2">
            <FileText className="w-3.5 h-3.5 text-gray-400 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-gray-600 dark:text-dark-muted leading-relaxed">
              {structure.description}
            </p>
          </div>
        )}

        {/* View relationship graph + open in catalog */}
        <div className="mt-3 flex gap-2">
          <button
            onClick={onViewGraph}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-white text-[10px] font-black uppercase tracking-widest transition-all hover:brightness-110 active:scale-[0.98]"
            style={{ background: categoryColor }}
          >
            <Network className="w-3 h-3" />
            {t('anatomy.view_graph')}
          </button>
          <Link
            to={`/catalogs?type=anatomy&item=${structure.id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all hover:bg-gray-100 dark:hover:bg-dark-bg text-gray-500 dark:text-dark-muted"
            title={t('anatomy.open_in_catalog', { defaultValue: 'Open in catalog' })}
          >
            <BookOpen className="w-3 h-3" />
            {t('anatomy.open_in_catalog', { defaultValue: 'Catalog' })}
          </Link>
        </div>

        {/* Interactive Parents / Children toggles */}
        <div className="mt-3 flex gap-3">
          <RelationToggle
            count={parentCount}
            label={t('anatomy.parents')}
            color={categoryColor}
            isOpen={showParents}
            onToggle={() => setShowParents((v) => !v)}
          />
          <RelationToggle
            count={childCount}
            label={t('anatomy.children')}
            color={categoryColor}
            isOpen={showChildren}
            onToggle={() => setShowChildren((v) => !v)}
          />
        </div>
      </div>

      {marker && (
        <div className="bg-gray-50 dark:bg-dark-bg rounded-2xl p-4">
          <p className="text-[10px] font-black uppercase text-gray-400 tracking-widest mb-3">
            {t('anatomy.location_preview')}
          </p>
          <OrganPreview figureSlug={markerFigure} marker={marker} label={structure.name} />
        </div>
      )}

      {/* Collapsible Parents panel */}
      <CollapsiblePanel open={showParents && parentCount > 0}>
        {renderRelatedList(parents, 'anatomy.parents_help')}
      </CollapsiblePanel>

      {/* Collapsible Children panel */}
      <CollapsiblePanel open={showChildren && childCount > 0}>
        {renderRelatedList(children, 'anatomy.children_help')}
      </CollapsiblePanel>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Interactive toggle for Parents / Children counts
// ---------------------------------------------------------------------------

interface RelationToggleProps {
  count: number;
  label: string;
  color: string;
  isOpen: boolean;
  onToggle: () => void;
}

const RelationToggle: React.FC<RelationToggleProps> = ({
  count,
  label,
  color,
  isOpen,
  onToggle,
}) => {
  const disabled = count === 0;
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-expanded={isOpen}
      className={`flex-1 flex items-center justify-center gap-2 rounded-lg py-2 transition-all ${
        disabled
          ? 'opacity-40 cursor-default bg-white/30 dark:bg-dark-bg/30'
          : 'cursor-pointer hover:bg-white dark:hover:bg-dark-bg'
      } ${isOpen && !disabled ? 'bg-white dark:bg-dark-bg' : 'bg-white/50 dark:bg-dark-bg/50'}`}
      style={
        isOpen && !disabled
          ? { boxShadow: `inset 0 0 0 2px ${color}` }
          : undefined
      }
    >
      <div className="text-center">
        <p className="text-xl font-bold text-gray-900 dark:text-dark-text leading-none">
          {count}
        </p>
        <p className="text-[10px] text-gray-400 uppercase tracking-wider mt-1">{label}</p>
      </div>
      {!disabled && (
        <ChevronDown
          className={`w-4 h-4 text-gray-400 transition-transform duration-300 ${
            isOpen ? 'rotate-180' : ''
          }`}
        />
      )}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Smooth collapsible panel (grid-rows trick — no JS height measurement)
// ---------------------------------------------------------------------------

const CollapsiblePanel: React.FC<{ open: boolean; children: React.ReactNode }> = ({
  open,
  children,
}) => (
  <div
    className={`grid transition-all duration-300 ease-out ${
      open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
    }`}
  >
    <div className="overflow-hidden">{children}</div>
  </div>
);
