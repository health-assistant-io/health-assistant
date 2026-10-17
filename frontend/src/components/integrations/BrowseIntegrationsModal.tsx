import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileText } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import {
  IntegrationCatalogGrid,
} from '../ui/integrations';
import {
  catalogEntry,
} from './integrationViews';
import type { IntegrationManifest } from '../../services/integrationService';

interface Props {
  open: boolean;
  available: IntegrationManifest[];
  connectedDomains?: Set<string>;
  onClose: () => void;
  onAdd: (domain: string) => void;
  onDocs: (domain: string) => void;
}

/**
 * Browse & Connect (plan 33 Phase D): the app-owned modal chrome hosts the
 * kit's IntegrationCatalogGrid — charter-category groups, controlled search
 * and install affordances. Install hands off to the parent's config flow;
 * Docs rides the entry-actions slot (app navigation).
 */
const BrowseIntegrationsModal: React.FC<Props> = ({ open, available, connectedDomains, onClose, onAdd, onDocs }) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');

  const entries = available.map((manifest) => ({
    ...catalogEntry(manifest),
    installedInstanceCount: connectedDomains?.has(manifest.domain) ? 1 : null,
  }));

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setQuery('');
          onClose();
        }
      }}
      title={t('integrations.browse.title', 'Browse & Connect')}
      size="xl"
    >
      <IntegrationCatalogGrid
        entries={entries}
        query={query}
        onQueryChange={setQuery}
        busy={false}
        labels={{
          searchLabel: t('integrations.browse.searchLabel', 'Search'),
          searchPlaceholder: t('integrations.browse.searchPlaceholder', 'Search integrations'),
          emptyLabel: t('integrations.browse.empty', 'No integrations available.'),
          noMatchesLabel: t('integrations.browse.noMatches', 'No matches.'),
          installLabel: t('integrations.browse.add', 'Add'),
          installedLabel: t('integrations.browse.connected', 'Connected'),
          categoryLabels: {
            files: t('integrations.categories.files', 'Files'),
            data: t('integrations.categories.data', 'Data'),
            search: t('integrations.categories.search', 'Search'),
            productivity: t('integrations.categories.productivity', 'Productivity'),
            utilities: t('integrations.categories.utilities', 'Utilities'),
            developer: t('integrations.categories.developer', 'Developer'),
            home: t('integrations.categories.home', 'Home'),
            learning: t('integrations.categories.learning', 'Learning'),
          },
        }}
        onInstall={(entry) => {
          onClose();
          onAdd(entry.id);
        }}
        renderEntryActions={(entry) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              onClose();
              onDocs(entry.id);
            }}
          >
            <FileText className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t('integrations.browse.docs', 'Docs')}
          </Button>
        )}
      />
    </Modal>
  );
};

export default BrowseIntegrationsModal;
