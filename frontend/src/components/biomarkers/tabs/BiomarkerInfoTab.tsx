/**
 * BiomarkerDetail "Clinical Significance" tab — read-only rendering of
 * `biomarker.info` (markdown or sanitized HTML). Editing lives in the Catalog
 * workspace (BiomarkerForm), so this surface is purely presentational.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Layers } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Biomarker } from '../../../types/biomarker';

interface BiomarkerInfoTabProps {
  biomarker: Biomarker;
}

export const BiomarkerInfoTab: React.FC<BiomarkerInfoTabProps> = ({ biomarker }) => {
  const { t } = useTranslation();

  return (
    <div className="p-8 animate-in fade-in duration-300">
      {biomarker.info ? (
        <div className="prose dark:prose-invert max-w-none text-gray-700 dark:text-dark-text leading-relaxed">
          {biomarker.info.includes('</') || biomarker.info.includes('<br') ? (
            <div
              className="font-medium"
              dangerouslySetInnerHTML={{ __html: biomarker.info }}
            />
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{biomarker.info}</ReactMarkdown>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-12 text-center opacity-60 border-2 border-dashed border-gray-100 dark:border-dark-border rounded-3xl">
          <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mb-4 text-gray-300">
            <Layers className="w-8 h-8" />
          </div>
          <p className="text-gray-400 font-bold uppercase tracking-widest text-xs">
            {t('biomarkers.no_clinical_info')}
          </p>
          <Link
            to={`/catalogs?type=biomarker&item=${biomarker.id}`}
            className="mt-4 text-blue-600 font-bold hover:underline text-sm uppercase tracking-tighter"
          >
            {t('biomarkers.add_to_catalog')}
          </Link>
        </div>
      )}
    </div>
  );
};
