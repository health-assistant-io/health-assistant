import { useTranslation } from 'react-i18next';
import { Zap } from 'lucide-react';
import DisplayBlockRenderer from './displayBlocks';
import type { ActionResult } from '../../services/integrationService';

interface Props {
  result: ActionResult;
}

/**
 * App-owned payload renderer for an integration action's result. The dialog
 * chrome (title, close, scroll body) comes from the family
 * ``ActionResultDialog``; this component only renders the extension-authored
 * display blocks inside its ``renderChrome`` slot.
 */
export default function ActionResultBlocks({ result }: Props) {
  const { t } = useTranslation();
  const blocks = result.results || [];

  return (
    <div className="space-y-6">
      {result.message && (
        <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-xl">
          <p className="text-sm text-blue-700 dark:text-blue-300 font-medium">{result.message}</p>
        </div>
      )}

      {blocks.length > 0 ? (
        blocks.map((block, idx) => (
          <div key={idx}>
            {block.title && (
              <h4 className="text-xs font-black text-gray-500 dark:text-dark-muted uppercase tracking-widest mb-2">
                {block.title}
              </h4>
            )}
            <DisplayBlockRenderer block={block} />
          </div>
        ))
      ) : (
        !result.message && (
          <p className="text-sm text-gray-400 italic text-center py-4 flex items-center justify-center gap-2">
            <Zap className="w-4 h-4" />
            {t('integrations.result.empty', 'Action completed. No structured result returned.')}
          </p>
        )
      )}
    </div>
  );
}
