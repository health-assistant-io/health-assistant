import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { integrationService } from '../../services/integrationService';
import { DocsDialog } from '../ui/integrations';
import { docsNodes, firstDocFile } from './integrationViews';

interface Props {
  domain: string;
  onClose: () => void;
}

/**
 * Integration documentation (plan 33 Phase D): the kit's DocsDialog owns the
 * tree chrome; the markdown fetch + render ride the app-owned content slot
 * (health's per-extension docs-tree.json is the precedent the component was
 * designed from).
 */
const IntegrationDocsDialog: React.FC<Props> = ({ domain, onClose }) => {
  const { t } = useTranslation();
  const [tree, setTree] = useState<Awaited<ReturnType<typeof integrationService.getDocumentation>>['tree']>(undefined);
  const [currentFile, setCurrentFile] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState<string>('');
  const [contentStatus, setContentStatus] = useState<'loading' | 'error' | 'ready'>('loading');
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    integrationService
      .getDocumentation(domain)
      .then((data) => {
        if (cancelled) return;
        setTree(data.tree);
        const first = firstDocFile(docsNodes(data.tree)) ?? 'README.md';
        setCurrentFile(first);
      })
      .catch((err) => {
        if (cancelled) return;
        setContentStatus('error');
        setErrorDetail(err?.response?.data?.detail || 'Failed to load documentation.');
      });
    return () => {
      cancelled = true;
    };
  }, [domain]);

  useEffect(() => {
    if (!currentFile || currentFile.startsWith('category:')) return;
    let cancelled = false;
    setContentStatus('loading');
    integrationService
      .getDocumentation(domain, currentFile)
      .then((data) => {
        if (cancelled) return;
        setMarkdown(data.markdown);
        setContentStatus('ready');
      })
      .catch((err) => {
        if (cancelled) return;
        setContentStatus('error');
        setErrorDetail(err?.response?.data?.detail || 'Failed to load documentation.');
      });
    return () => {
      cancelled = true;
    };
  }, [domain, currentFile]);

  const nodes = docsNodes(tree);

  return (
    <DocsDialog
      open
      onOpenChange={(open) => !open && onClose()}
      docs={nodes}
      selectedId={currentFile}
      onSelect={(nodeId) => {
        if (!nodeId.startsWith('category:')) setCurrentFile(nodeId);
      }}
      status={contentStatus}
      errorDetail={errorDetail}
      renderContent={() => (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
        </div>
      )}
      labels={{
        title: t('integrations.docs.title', 'Documentation'),
        closeLabel: t('integrations.docs.close', 'Close'),
        treeLabel: t('integrations.docs.tree', 'Documentation'),
        emptyLabel: t('integrations.docs.empty', 'No documentation provided.'),
        loadingLabel: t('integrations.docs.loading', 'Loading…'),
        errorLabel: t('integrations.docs.error', 'Failed to load documentation.'),
      }}
    />
  );
};

export default IntegrationDocsDialog;
