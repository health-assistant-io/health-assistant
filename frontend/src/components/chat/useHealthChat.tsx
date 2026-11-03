import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';

import {
  deleteChatSession,
  getChatSessionMessages,
  listChatSessions,
  renameChatSession,
  type ChatSession,
} from '../../services/aiAssistanceService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { useChatAttachments } from '../../hooks/useChatAttachments';
import type { PendingChatAttachment, TaskInfo, ToolCallInfo } from '../../types/ai';
import { TERMINAL_HITL_STATUSES } from '../ai/hitl/registry';
import { createChatTransport, type HealthChatTransport } from './chatTransport';
import { resolveStreamErrorMessage } from './streamErrors';
import { useChatStream, type ChatMessageView } from '../ui/chat';

/**
 * One persisted/optimistic chat row. Server rows carry the DB id; optimistic
 * user rows use a ``local-`` prefixed id until the post-turn refetch
 * replaces them with the persisted pair.
 */
export interface ChatRow {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  images?: string[];
  toolCalls?: ToolCallInfo[];
  citations?: string[];
  tasks?: TaskInfo[];
  /** Loaded from server history — suppresses HITL auto-resume. */
  fromHistory?: boolean;
  /** Branch decoration (family contract, plan 13 H2). */
  parentId?: string | null;
  variantIndex?: number;
  variantCount?: number;
  siblingIds?: string[];
}

/**
 * useChatStream rejects blank input, but image-only turns legitimately
 * carry an empty string — the real input travels through the transport's
 * pending-send staging, so the hook only ever needs a non-blank sentinel.
 */
const SEND_SENTINEL = '‍';

const EMPTY_EXTRAS = { citations: [] as string[], tasks: [] as TaskInfo[] };

function toRow(m: Awaited<ReturnType<typeof getChatSessionMessages>>[number]): ChatRow {
  return {
    id: m.id,
    role: m.role,
    content: m.content.text,
    images: m.content.images,
    toolCalls: m.tool_calls?.map((tc) => ({
      name: tc.name,
      args: typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args),
      result: tc.result,
      status: 'finished' as const,
    })),
    citations: m.citations,
    tasks: m.tasks,
    fromHistory: true,
    parentId: m.parent_id,
    variantIndex: m.variant_index,
    variantCount: m.variant_count,
    siblingIds: m.sibling_ids,
  };
}

export interface UseHealthChatOptions {
  isFullScreen: boolean;
  initialSessionId?: string;
  /** Extra context entries for the next chat turn (current tab, …). */
  getContext?: () => Record<string, unknown>;
}

/**
 * Health chat wiring shared by the drawer and the full page: sessions and
 * rows are component state synced with the UI store's session pointer, the
 * live turn runs on the library's `useChatStream` through the SSE adapter
 * (`chatTransport.ts`), and HITL continuation turns reuse the same stream
 * via the transport's resume intent. Provided once per surface.
 */
export function useHealthChat(options: UseHealthChatOptions) {
  const { isFullScreen, initialSessionId } = options;
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [rows, setRows] = useState<ChatRow[]>([]);
  const [draft, setDraft] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingChatAttachment[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  const [liveExtras, setLiveExtras] = useState<{ citations: string[]; tasks: TaskInfo[] }>(
    EMPTY_EXTRAS,
  );
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  const busyRef = useRef(false);
  const resumedRowIds = useRef<Set<string>>(new Set());
  const committedTurnRef = useRef<number | null>(null);
  const pendingSendRef = useRef<{ text: string; images: string[] } | null>(null);
  /** The intent of the in-flight turn (plan 13 H2): branched turns commit by
   * refetching the re-walked server rows instead of the local append. */
  const turnIntentRef = useRef<'send' | 'edit' | 'regenerate' | 'resume'>('send');
  const rowsRef = useRef<ChatRow[]>([]);
  rowsRef.current = rows;
  const { addFiles } = useChatAttachments((m: string) => toast.warning(m));

  const currentPatient = usePatientStore((state) => state.currentPatient);
  const pendingAIMessage = useUIStore((state) => state.pendingAIMessage);
  const setPendingAIMessage = useUIStore((state) => state.setPendingAIMessage);
  const currentAiSessionId = useUIStore((state) => state.currentAiSessionId);
  const setCurrentAiSessionId = useUIStore((state) => state.setCurrentAiSessionId);

  const isFullScreenRef = useRef(isFullScreen);
  isFullScreenRef.current = isFullScreen;
  const initialSessionIdRef = useRef(initialSessionId);
  initialSessionIdRef.current = initialSessionId;

  const localizeError = useCallback(
    (msg: { error?: string; errorType?: string }) => resolveStreamErrorMessage(t, msg),
    [t],
  );  const getContext = options.getContext;
  const contextRef = useRef(getContext);
  contextRef.current = getContext;

  const transportRef = useRef<HealthChatTransport | null>(null);
  if (transportRef.current === null) {
    transportRef.current = createChatTransport({
      getSessionId: () => currentSessionIdRef.current,
      getContext: () => {
        const patient = usePatientStore.getState().currentPatient;
        const ui = useUIStore.getState();
        return {
          patient_id: patient?.id,
          examination_id: ui.currentExaminationId,
          biomarker_id: ui.currentBiomarkerId,
          medication_id: ui.currentMedicationId,
          allergy_id: ui.currentAllergyId,
          ...(contextRef.current?.() ?? {}),
        };
      },
      getPendingSend: () => pendingSendRef.current,
      clearPendingSend: () => {
        pendingSendRef.current = null;
      },
      onSession: (sessionId) => {
        const isNew = sessionId !== currentSessionIdRef.current;
        currentSessionIdRef.current = sessionId;
        setCurrentSessionId(sessionId);
        if (isNew) {
          void fetchSessions();
          if (isFullScreenRef.current) {
            navigate(`/ai-assistant/${sessionId}`);
          }
        }
      },
      onCitation: (source) => {
        setLiveExtras((prev) =>
          prev.citations.includes(source)
            ? prev
            : { ...prev, citations: [...prev.citations, source] },
        );
      },
      onTask: (task) => {
        setLiveExtras((prev) => {
          const idx = prev.tasks.findIndex((x) => x.proposal_id === task.proposal_id);
          if (idx === -1) return { ...prev, tasks: [...prev.tasks, task] };
          const tasks = prev.tasks.slice();
          tasks[idx] = task;
          return { ...prev, tasks };
        });
      },
      localizeError,
    });
  }
  const stream = useChatStream({ transport: transportRef.current, timeoutMs: 300_000 });
  const streamSend = stream.send;
  const streamReset = stream.reset;
  const sending = stream.status === 'pending' || stream.status === 'streaming';
  const sendingRef = useRef(sending);
  sendingRef.current = sending;

  const fetchSessions = useCallback(async () => {
    try {
      const patient = usePatientStore.getState().currentPatient;
      setSessions(await listChatSessions(patient?.id));
    } catch (err) {
      console.error('Failed to fetch sessions', err);
    }
  }, []);

  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const loadSession = useCallback(
    async (sessionId: string) => {
      if (!sessionId) return;
      busyRef.current = true;
      try {
        const data = await getChatSessionMessages(sessionId);
        setRows(data.map(toRow));
        currentSessionIdRef.current = sessionId;
        setCurrentSessionId(sessionId);
        setIsHistoryOpen(false);
        resumedRowIds.current.clear();
        if (isFullScreenRef.current && initialSessionIdRef.current !== sessionId) {
          navigateRef.current(`/ai-assistant/${sessionId}`);
        }
      } catch (err) {
        console.error('Failed to load session', err);
      } finally {
        busyRef.current = false;
      }
    },
    // Stable identity on purpose: `navigate` changes identity on every
    // location change, and a churning `loadSession` re-fires the session
    // effects right after a New Chat navigation — reloading the session
    // the user just left (regression, 2026-10-10).
    [],
  );

  const startNewChat = useCallback(() => {
    setRows([]);
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    // Null the shared pointer SYNCHRONOUSLY with the click: leaving it to
    // the sync-back effect keeps a one-commit window where the store still
    // names the abandoned session and the store-sync effect reloads it
    // (startNewChat regression, 2026-10-10). Other mounted surfaces reset
    // through the same store write.
    setCurrentAiSessionId(null);
    setIsHistoryOpen(false);
    resumedRowIds.current.clear();
    setLiveExtras(EMPTY_EXTRAS);
    if (isFullScreenRef.current) {
      navigateRef.current('/ai-assistant');
    }
  }, [setCurrentAiSessionId]);

  const deleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await deleteChatSession(sessionId);
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        if (currentSessionIdRef.current === sessionId) {
          startNewChat();
        }
      } catch (err) {
        console.error('Failed to delete session', err);
      }
    },
    [startNewChat],
  );

  const renameSession = useCallback(async (sessionId: string, title: string) => {
    const updated = await renameChatSession(sessionId, title);
    setSessions((prev) => prev.map((s) => (s.id === sessionId ? updated : s)));
    return updated;
  }, []);

  /** Resolve the SERVER message id for a row's HITL cluster. Live rows use
   * optimistic ``local-`` ids — the server row is found by its proposal_id
   * (proposal ids are unique within a session), keeping the resume
   * branch-correct (a card on branch A resumes branch A — plan 13 H2). */
  const resolveServerRowId = useCallback(async (row: ChatRow): Promise<string | null> => {
    if (!row.id.startsWith('local-')) return row.id;
    const sessionId = currentSessionIdRef.current;
    const proposalId = row.tasks?.find((t) => t.proposal_id)?.proposal_id;
    if (!sessionId || !proposalId) return null;
    try {
      const server = await getChatSessionMessages(sessionId);
      const hit = server.find((m) =>
        (m.tasks ?? []).some((t) => t.proposal_id === proposalId),
      );
      return hit?.id ?? null;
    } catch {
      return null;
    }
  }, []);

  /** Trigger the HITL continuation turn after a message's cards resolved. */
  const triggerResume = useCallback(
    async (row: ChatRow) => {
      const sessionId = currentSessionIdRef.current;
      if (!sessionId) return;
      if (resumedRowIds.current.has(row.id)) return;
      resumedRowIds.current.add(row.id);
      // Branch guard: the card's row must still be on the visible path —
      // a terminal card on branch A never resumes while branch B is
      // selected (plan 13 H2 gate: HITL stays linear-agnostic).
      if (!rowsRef.current.some((r) => r.id === row.id)) return;
      // Race guard: don't fire a resume while another stream is running.
      if (sendingRef.current || busyRef.current) return;
      const messageId = await resolveServerRowId(row);
      busyRef.current = true;
      turnIntentRef.current = 'resume';
      setLiveExtras(EMPTY_EXTRAS);
      transportRef.current?.beginResume(messageId ?? undefined);
      await streamSend(SEND_SENTINEL);
    },
    [resolveServerRowId, streamSend],
  );

  /** Resolve one HITL task card and auto-resume when the cluster is done. */
  const resolveTask = useCallback(
    (row: ChatRow | null, proposalId: string, updated: TaskInfo) => {
      if (row === null) {
        setLiveExtras((prev) => ({
          ...prev,
          tasks: prev.tasks.map((x) => (x.proposal_id === proposalId ? updated : x)),
        }));
        return;
      }
      const currentTasks = row.tasks ?? [];
      const nextTasks = currentTasks.map((x) => (x.proposal_id === proposalId ? updated : x));
      setRows((prev) =>
        prev.map((r) => (r.id === row.id ? { ...r, tasks: nextTasks } : r)),
      );
      const allTerminal =
        nextTasks.length > 0 && nextTasks.every((x) => TERMINAL_HITL_STATUSES.has(x.status));
      if (allTerminal && !row.fromHistory && !resumedRowIds.current.has(row.id)) {
        void triggerResume(row);
      }
    },
    [triggerResume],
  );

  const submit = useCallback(
    async (overrideContent?: string) => {
      const hasImages = pendingAttachments.some((a) => a.status === 'ready');
      const text = overrideContent ?? draft;
      if ((!text.trim() && !overrideContent && !hasImages) || sendingRef.current) return;
      const images = pendingAttachments
        .filter((a) => a.status === 'ready' && a.dataUrl)
        .map((a) => a.dataUrl as string);
      const messageContent = overrideContent ? overrideContent : text.trim();
      if (!overrideContent) setDraft('');
      setPendingAttachments([]);
      pendingSendRef.current = { text: messageContent, images };
      turnIntentRef.current = 'send';
      busyRef.current = true;
      setLiveExtras(EMPTY_EXTRAS);
      setRows((prev) => [
        ...prev,
        {
          id: `local-${Date.now()}`,
          role: 'user',
          content: messageContent,
          ...(images.length ? { images } : {}),
        },
      ]);
      await streamSend(messageContent.trim() !== '' ? messageContent : SEND_SENTINEL);
    },
    [draft, pendingAttachments, streamSend],
  );

  /** Edit-and-resend as a branch (plan 13 H2): the edited text re-sends as
   * a sibling variant; the done-turn refetch swaps the visible path onto
   * the new branch. */
  const submitEdit = useCallback(
    async (messageId: string, content: string) => {
      if (content.trim() === '' || sendingRef.current || busyRef.current) return;
      pendingSendRef.current = { text: content, images: [] };
      turnIntentRef.current = 'edit';
      busyRef.current = true;
      setLiveExtras(EMPTY_EXTRAS);
      transportRef.current?.beginEdit(messageId);
      await streamSend(content);
    },
    [streamSend],
  );

  /** Regenerate as a branch (plan 13 H2): a fresh assistant sibling under
   * the target user message — earlier replies stay switchable variants. */
  const regenerate = useCallback(
    async (messageId: string) => {
      if (sendingRef.current || busyRef.current) return;
      turnIntentRef.current = 'regenerate';
      busyRef.current = true;
      setLiveExtras(EMPTY_EXTRAS);
      transportRef.current?.beginRegenerate(messageId);
      await streamSend(SEND_SENTINEL);
    },
    [streamSend],
  );

  /** The error-tile retry (H2): re-roll the last user message — branch,
   * never overwrite. */
  const retry = useCallback(async () => {
    if (sendingRef.current || busyRef.current) return;
    const lastUser = [...rowsRef.current].reverse().find((r) => r.role === 'user');
    if (!lastUser || lastUser.id.startsWith('local-')) return;
    await regenerate(lastUser.id);
  }, [regenerate]);

  /** Flip one branch pointer and adopt the re-walked path (plan 13 H2). */
  const selectVariant = useCallback(async (messageId: string) => {
    if (sendingRef.current || busyRef.current) return;
    try {
      const messages = await transportRef.current?.select(messageId);
      if (messages) {
        resumedRowIds.current.clear();
        setRows(messages.map(toRow));
      }
    } catch (err) {
      console.error('Failed to select variant', err);
    }
  }, []);

  const handleFilesAdded = useCallback(
    async (files: FileList | File[]) => {
      const next = await addFiles(files, pendingAttachments);
      setPendingAttachments(next);
    },
    [addFiles, pendingAttachments],
  );

  useEffect(() => {
    void fetchSessions();
  }, [currentPatient?.id, fetchSessions]);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
    setCurrentAiSessionId(currentSessionId);
  }, [currentSessionId, setCurrentAiSessionId]);

  // Terminal turns commit the streamed assistant row LOCALLY (the current
  // contract: HITL auto-resume only fires for turns streamed in this
  // session — refetching would flip them to history rows and silence the
  // continuation). Server truth replaces local rows on session
  // load/switch. Error turns keep the live error tile until the next send
  // (career's contract — no fake assistant message). `interrupted` is the
  // resumable HITL pause and stays live until the synthetic flow_finished
  // at stream close flips it to done.
  //
  // Plan 13 H2: EDIT/REGENERATE turns commit by refetching the re-walked
  // server rows (variant decoration needs real ids) — the trailing rows of
  // the new branch are un-marked from history so the HITL auto-resume
  // contract survives branching. The turn's citations/tasks attach to the
  // trailing assistant row. Error turns refetch too (retry-as-branch needs
  // the persisted user-message id) while keeping the error tile.
  const status = stream.status;
  useEffect(() => {
    if (status !== 'done' && status !== 'error') return;
    // One commit per turn: StrictMode double-invokes effects with the same
    // render closure — the startedAt identity separates turns.
    if (stream.startedAt !== null && committedTurnRef.current === stream.startedAt) {
      return;
    }
    if (stream.startedAt !== null) {
      committedTurnRef.current = stream.startedAt;
    }
    const intent = turnIntentRef.current;
    turnIntentRef.current = 'send';

    const refetchBranchedRows = async () => {
      const sessionId = currentSessionIdRef.current;
      if (!sessionId) return;
      try {
        const data = await getChatSessionMessages(sessionId);
        const rows = data.map(toRow);
        // Un-mark the trailing rows of THIS turn (from the last user row
        // on) so auto-resume still fires for cards it emitted — the H1
        // contract, preserved through branched turns.
        const lastUserIdx = rows.map((r) => r.role).lastIndexOf('user');
        for (let i = Math.max(lastUserIdx, 0); i < rows.length; i++) {
          rows[i] = { ...rows[i], fromHistory: false };
        }
        const trailingAssistant = rows[rows.length - 1];
        if (trailingAssistant && trailingAssistant.role === 'assistant') {
          trailingAssistant.citations = liveExtras.citations;
          trailingAssistant.tasks = liveExtras.tasks;
        }
        setRows(rows);
      } catch (err) {
        console.error('Failed to refetch branched session', err);
      }
    };

    if (status === 'error') {
      if (intent !== 'resume') {
        void refetchBranchedRows();
      }
      busyRef.current = false;
      return;
    }

    if (intent === 'edit' || intent === 'regenerate') {
      void refetchBranchedRows().then(() => {
        setLiveExtras(EMPTY_EXTRAS);
        streamReset();
      });
      busyRef.current = false;
      return;
    }

    const assistantRow: ChatRow = {
      id: `local-a-${Date.now()}`,
      role: 'assistant',
      content: stream.text ?? '',
      toolCalls: stream.toolCalls.map((call) => ({
        name: call.name,
        args: call.args,
        result: call.result,
        status: call.status === 'done' ? 'finished' : 'executing',
      })),
      citations: liveExtras.citations,
      tasks: liveExtras.tasks,
    };
    setRows((prev) => [...prev, assistantRow]);
    setLiveExtras(EMPTY_EXTRAS);
    streamReset();
    busyRef.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, streamReset]);

  useEffect(() => {
    if (pendingAIMessage) {
      void submit(pendingAIMessage);
      setPendingAIMessage(null);
    }
  }, [pendingAIMessage, setPendingAIMessage, submit]);

  // Sync local currentSessionId with the store if it changes elsewhere
  // (the other surface — drawer ↔ page — switched sessions).
  useEffect(() => {
    if (!busyRef.current && currentAiSessionId !== currentSessionIdRef.current) {
      if (currentAiSessionId) {
        void loadSession(currentAiSessionId);
      } else if (currentSessionIdRef.current) {
        setRows([]);
        currentSessionIdRef.current = null;
        setCurrentSessionId(null);
      }
    }
  }, [currentAiSessionId, loadSession]);

  // Priority 1: session id from the URL; base-URL visit on the page →
  // fresh chat. The store-pointer sync (drawer ↔ page) is the store-sync
  // effect's job — deliberately NOT a dep here: a New Chat navigation is a
  // router transition that lands AFTER the local reset commits, and a
  // store-driven re-run in that window would reload the session the user
  // just left (startNewChat regression, 2026-10-10).
  useEffect(() => {
    if (initialSessionId) {
      if (initialSessionId !== currentSessionIdRef.current) {
        void loadSession(initialSessionId);
      }
    } else if (currentSessionIdRef.current && isFullScreen && !initialSessionId) {
      setRows([]);
      currentSessionIdRef.current = null;
      setCurrentSessionId(null);
      setIsHistoryOpen(false);
    }
  }, [initialSessionId, isFullScreen, loadSession, setCurrentSessionId, setIsHistoryOpen, setRows]);

  const viewMessages = rows.map<ChatMessageView>((row) => ({
    id: row.id,
    role: row.role,
    content: row.content,
    status: 'done',
    meta: { row },
    parentId: row.parentId ?? null,
    variants:
      row.variantCount && row.variantCount > 1
        ? {
            index: row.variantIndex ?? 1,
            count: row.variantCount,
            siblingIds: row.siblingIds ?? [row.id],
          }
        : undefined,
  }));

  return {
    t,
    rows,
    viewMessages,
    stream,
    sending,
    draft,
    setDraft,
    pendingAttachments,
    setPendingAttachments,
    handleFilesAdded,
    liveExtras,
    resolveTask,
    triggerResume,
    resumedRowIds,
    submit,
    submitEdit,
    regenerate,
    retry,
    selectVariant,
    sessions,
    currentSessionId,
    isHistoryOpen,
    setIsHistoryOpen,
    fetchSessions,
    loadSession,
    startNewChat,
    deleteSession,
    renameSession,
  };
}

export type HealthChat = ReturnType<typeof useHealthChat>;

export const HealthChatContext = createContext<HealthChat | null>(null);

export function HealthChatProvider({
  children,
  ...options
}: UseHealthChatOptions & { children: React.ReactNode }) {
  const chat = useHealthChat(options);
  return <HealthChatContext.Provider value={chat}>{children}</HealthChatContext.Provider>;
}

export function useHealthChatContext(): HealthChat {
  const chat = useContext(HealthChatContext);
  if (chat === null) {
    throw new Error('useHealthChatContext requires a HealthChatProvider ancestor');
  }
  return chat;
}
