import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, Heart } from 'lucide-react';
import {
  CareerMark,
  HealthMark,
  Modal,
  ModalContent,
  NeuronectionMark,
  NeuronectionWordmark,
  SponsorCard,
  StudyMark,
} from '@neuronection/assistant-ui';
import type { LogoProps } from '@neuronection/assistant-ui';
import packageJson from '../../../package.json';

import { NEURONECTION_URL, SPONSOR_CHANNELS } from '../../config/funding';

const fundPillClass =
  'inline-flex h-8 items-center gap-1.5 rounded-full border border-rose-100 bg-rose-50 px-4 text-[13px] font-medium text-rose-600 transition-colors hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/40 [&_svg]:size-4';
const aboutPillClass =
  'inline-flex h-8 items-center rounded-full px-4 text-[13px] font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white dark:focus-visible:outline-blue-400';
const fundPillCompactClass =
  'inline-flex h-7 items-center gap-1 rounded-full border border-rose-100 bg-rose-50 px-3 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/40 [&_svg]:size-3.5';
const aboutPillCompactClass =
  'inline-flex h-7 items-center rounded-full px-3 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:bg-white/5 dark:hover:text-white dark:focus-visible:outline-blue-400';

/** Family assistants listed in the footer; every row links to its site. */
const FAMILY_LINKS: {
  name: string;
  Mark: React.ComponentType<LogoProps>;
  href: string;
  current?: boolean;
}[] = [
  { name: 'Health', Mark: HealthMark, href: 'https://health-assistant.io', current: true },
  { name: 'Career', Mark: CareerMark, href: 'https://neuronection.com/en/career/' },
  { name: 'Study', Mark: StudyMark, href: 'https://neuronection.com/en/study/' },
];

/**
 * Sidebar footer project block: family branding, the three family
 * assistants, About and Fund actions plus the version. Presentational
 * glue on library primitives — copy comes from i18n, channels from
 * config (ADR-006 keeps this app-side). Collapses to icons in the rail;
 * `compact` (short viewports) drops the branding + family panel and
 * slims the pills so the nav list keeps the space.
 */
export function SidebarFooter({ collapsed, compact = false }: { collapsed: boolean; compact?: boolean }) {
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
    <div className="flex flex-col items-center gap-2.5">
      {!compact && (
        <a
          href={NEURONECTION_URL}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 self-start text-xs text-gray-500 transition-colors hover:text-blue-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-400 dark:hover:text-blue-400"
        >
          <span>{t('footer.part_of', 'Part of')}</span>
          <NeuronectionMark size={16} />
          <NeuronectionWordmark height={14} />
        </a>
      )}
      {!compact && (
        <div className="w-full rounded-[var(--as-radius)] bg-gray-50 px-2 pb-1.5 pt-2 dark:bg-white/5">
          <p className="px-1.5 pb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-500">
            {t('footer.more_from_family', 'More from the family')}
          </p>
          {FAMILY_LINKS.map(({ name, Mark, href, current }) => (
            <a
              key={href}
              href={href}
              target="_blank"
              rel="noreferrer"
              aria-label={`${name} ${t('footer.assistant', 'Assistant')}`}
              className="group flex items-center gap-2 rounded-[var(--as-radius-sm)] px-1.5 py-1 text-sm transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:hover:bg-white/5 dark:focus-visible:outline-blue-400"
            >
              <Mark size={16} />
              <span
                className={`w-12 text-left ${
                  current
                    ? 'font-bold text-gray-900 dark:text-white'
                    : 'font-medium text-gray-700 dark:text-gray-200'
                }`}
              >
                {name}
              </span>
              <span className="font-medium text-gray-500 dark:text-gray-400">
                {t('footer.assistant', 'Assistant')}
              </span>
              <ArrowUpRight
                aria-hidden
                className="ml-auto size-3.5 text-gray-300 transition-colors group-hover:text-blue-600 dark:text-gray-600 dark:group-hover:text-blue-400"
              />
            </a>
          ))}
        </div>
      )}
      {compact ? (
        <>
          <div className="flex w-full items-center justify-between">
            <a
              href={NEURONECTION_URL}
              target="_blank"
              rel="noreferrer"
              title={t('footer.part_of_family', 'Part of Neuronection')}
              aria-label={t('footer.part_of_family', 'Part of Neuronection')}
              className="flex flex-col items-center gap-1 text-gray-400 transition-colors hover:text-blue-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:text-gray-500 dark:hover:text-blue-400"
            >
              <NeuronectionMark size={20} />
              <NeuronectionWordmark height={7} />
            </a>
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
              v{packageJson.version}
            </span>
          </div>
          <div className="flex items-center justify-center gap-2">
            <button
              type="button"
              onClick={() => setFundOpen(true)}
              className={fundPillCompactClass}
            >
              <Heart />
              {t('footer.fund', 'Fund')}
            </button>
            <button
              type="button"
              onClick={() => navigate('/about')}
              className={aboutPillCompactClass}
            >
              {t('common.about', 'About')}
            </button>
          </div>
        </>
      ) : (
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setFundOpen(true)} className={fundPillClass}>
            <Heart />
            {t('footer.fund', 'Fund')}
          </button>
          <button type="button" onClick={() => navigate('/about')} className={aboutPillClass}>
            {t('common.about', 'About')}
          </button>
        </div>
      )}
      {!compact && (
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400">
          v{packageJson.version}
        </p>
      )}

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
