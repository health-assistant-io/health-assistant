import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Heart } from 'lucide-react';
import {
  FamilyMenu,
  Modal,
  ModalContent,
  NeuronectionMark,
  NeuronectionWordmark,
  SponsorCard,
} from '@neuronection/assistant-ui';
import packageJson from '../../../package.json';

import { NEURONECTION_URL, SPONSOR_CHANNELS } from '../../config/funding';

const fundPillClass =
  'inline-flex h-8 items-center gap-1.5 rounded-full border border-rose-100 bg-rose-50 px-3 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/40 [&_svg]:size-4';
const aboutPillClass =
  'inline-flex h-8 items-center rounded-full px-3 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white dark:focus-visible:outline-blue-400';
const fundPillCompactClass =
  'inline-flex h-7 items-center gap-1 rounded-full border border-rose-100 bg-rose-50 px-2.5 text-[11px] font-medium text-rose-600 transition-colors hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/40 [&_svg]:size-3.5';
const aboutPillCompactClass =
  'inline-flex h-7 items-center rounded-full px-2.5 text-[11px] font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white dark:focus-visible:outline-blue-400';

/**
 * Sidebar footer project block: family branding, the family switcher,
 * About and Fund actions plus the version. Copy comes from i18n and the
 * sponsor channels from config (ADR-006 keeps this app-side). Collapses
 * to icons in the rail; `compact` (short viewports) slims the pills.
 */
export function SidebarFooter({
  collapsed,
  compact = false,
}: {
  collapsed: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [fundOpen, setFundOpen] = useState(false);

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1.5">
        <a
          href={NEURONECTION_URL}
          target="_blank"
          rel="noreferrer"
          title={t('footer.part_of_family', 'Part of Neuronection')}
          aria-label={t('footer.part_of_family', 'Part of Neuronection')}
          className="flex rounded-lg p-1.5 text-gray-400 transition-colors hover:text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-500 dark:hover:text-blue-400"
        >
          <NeuronectionMark size={18} />
        </a>
        <button
          type="button"
          title={t('footer.support_project', 'Support this project')}
          aria-label={t('footer.support_project', 'Support this project')}
          onClick={() => setFundOpen(true)}
          className="flex size-7 items-center justify-center rounded-full text-rose-500 transition-colors hover:bg-rose-50 hover:text-rose-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 dark:hover:bg-rose-950/40"
        >
          <Heart className="size-4" />
        </button>
        <span className="text-[11px] font-medium text-gray-500 dark:text-gray-400">
          v{packageJson.version.split('-')[0]}
        </span>
        <Modal open={fundOpen} onOpenChange={setFundOpen}>
          <ModalContent size="sm" aria-describedby={undefined}>
            <SponsorCard
              channels={SPONSOR_CHANNELS}
              title={t('sponsor.title', 'Help Health Assistant grow')}
              columns={1}
              className="border-none bg-transparent shadow-none"
            />
          </ModalContent>
        </Modal>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <a
        href={NEURONECTION_URL}
        target="_blank"
        rel="noreferrer"
        aria-label={t('footer.part_of_family', 'Part of Neuronection')}
        className="flex min-w-0 max-w-full flex-wrap self-start items-center gap-x-1 gap-y-0.5 text-[11px] text-gray-500 transition-colors hover:text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:text-blue-400"
      >
        <span>{t('footer.part_of', 'Part of')}</span>
        <NeuronectionMark size={16} />
        <NeuronectionWordmark height={12} />
        <span className="text-gray-400 dark:text-gray-500">
          {t('footer.family', 'family')}
        </span>
      </a>
      <div className="flex flex-wrap items-center justify-center gap-1.5">
        <FamilyMenu
          current="health"
          label={t('footer.explore', 'Explore')}
          ariaLabel={t('footer.explore_projects', 'Explore projects')}
          menuLabel={t('footer.neuronection_family', 'Neuronection family')}
          familyLabel={t('footer.family', 'family')}
          countLabel={(count) => t('footer.app_count', { count })}
          closeLabel={t('common.close', 'Close')}
          currentLabel={t('footer.current', 'Current')}
          version={`v${packageJson.version}`}
          className={compact ? 'h-7 px-2 text-[11px]' : undefined}
        />
        <button
          type="button"
          onClick={() => setFundOpen(true)}
          className={compact ? fundPillCompactClass : fundPillClass}
        >
          <Heart />
          {t('footer.fund', 'Fund')}
        </button>
        <button
          type="button"
          onClick={() => navigate('/about')}
          className={compact ? aboutPillCompactClass : aboutPillClass}
        >
          {t('common.about', 'About')}
        </button>
      </div>
      <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400">
        v{packageJson.version}
      </span>

      <Modal open={fundOpen} onOpenChange={setFundOpen}>
        <ModalContent size="sm" aria-describedby={undefined}>
          <SponsorCard
            channels={SPONSOR_CHANNELS}
            title={t('sponsor.title', 'Help Health Assistant grow')}
            columns={1}
            className="border-none bg-transparent shadow-none"
          />
        </ModalContent>
      </Modal>
    </div>
  );
}
