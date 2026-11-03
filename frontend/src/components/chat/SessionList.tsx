import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChatSessionList } from '../ui/chat';
import { useUIStore } from '../../store/slices/uiSlice';
import { Button } from '../ui/Button';
import {
  Modal,
  ModalContent,
  ModalHeader,
  ModalTitle,
  ModalFooter,
} from '@neuronection/assistant-ui';
import { exportChatMarkdown } from './exportChat';
import { useHealthChatContext } from './useHealthChat';

/**
 * App-side session management on the library `ChatSessionList`: rename
 * dialog, delete confirmation and .md export stay app glue — the list
 * itself (search, date groups, row actions) is the library module.
 * Shared by the /ai-assistant page aside and the mobile history overlay.
 */
export const SessionList: React.FC<{ onPick?: () => void }> = ({ onPick }) => {
  const { t } = useTranslation();
  const chat = useHealthChatContext();
  const showConfirmation = useUIStore((state) => state.showConfirmation);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const submitRename = async () => {
    const title = renameDraft.trim();
    if (renameId === null || title === '') return;
    setBusy(true);
    try {
      await chat.renameSession(renameId, title);
      setRenameId(null);
    } catch (err) {
      console.error('Failed to rename session', err);
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = (sessionId: string) => {
    showConfirmation({
      title: t('ai_chat.sessions.delete_title'),
      message: t('ai_chat.sessions.delete_body'),
      confirmLabel: t('ai_chat.sessions.delete_confirm'),
      confirmVariant: 'danger',
      onConfirm: async () => {
        await chat.deleteSession(sessionId);
      },
    });
  };

  return (
    <>
      <ChatSessionList
        sessions={chat.sessions.map((session) => ({
          id: session.id,
          title: session.title || t('ai_chat.status.untitled_consultation'),
          updatedAt: session.updated_at,
        }))}
        activeId={chat.currentSessionId}
        onSelect={(id) => {
          void chat.loadSession(id);
          onPick?.();
        }}
        onNew={() => {
          chat.startNewChat();
          onPick?.();
        }}
        onRename={(id) => {
          setRenameId(id);
          setRenameDraft(
            chat.sessions.find((session) => session.id === id)?.title ?? '',
          );
        }}
        onDelete={(id) => confirmDelete(id)}
        onExport={(id) => {
          const session = chat.sessions.find((candidate) => candidate.id === id);
          void exportChatMarkdown(
            id,
            session?.title || t('ai_chat.status.untitled_consultation'),
            t('ai_chat.header.title'),
          );
        }}
        labels={{
          searchPlaceholder: t('common.search'),
          newChat: t('ai_chat.tooltips.new_chat'),
          rename: t('ai_chat.sessions.rename'),
          delete: t('ai_chat.sessions.delete'),
          export: t('ai_chat.sessions.export'),
          today: t('ai_chat.sessions.today'),
          yesterday: t('ai_chat.sessions.yesterday'),
          earlier: t('ai_chat.sessions.earlier'),
          empty: t('ai_chat.sessions.empty'),
          noResults: t('common.no_results'),
          search: t('common.search'),
        }}
      />
      <Modal open={renameId !== null} onOpenChange={(open) => !open && setRenameId(null)}>
        <ModalContent size="sm">
          <ModalHeader>
            <ModalTitle>{t('ai_chat.sessions.rename_title')}</ModalTitle>
          </ModalHeader>
          <input
            data-testid="session-rename-input"
            aria-label={t('ai_chat.sessions.name_aria')}
            value={renameDraft}
            maxLength={200}
            autoFocus
            onChange={(e) => setRenameDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                void submitRename();
              }
            }}
            className="w-full rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-surface px-3 py-2 text-sm text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-indigo-500/20 outline-none"
          />
          <ModalFooter>
            <Button variant="outline" onClick={() => setRenameId(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="default"
              disabled={busy || renameDraft.trim() === ''}
              onClick={() => void submitRename()}
            >
              {t('common.save')}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
};
