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
  );

  const getContext = options.getContext;
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
          navigate(`/ai-assistant/${sessionId}`);
        }
      } catch (err) {
        console.error('Failed to load session', err);
      } finally {
        busyRef.current = false;
      }
    },
    [navigate],
  );

  const startNewChat = useCallback(() => {
    setRows([]);
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    setIsHistoryOpen(false);
    resumedRowIds.current.clear();
    setLiveExtras(EMPTY_EXTRAS);
    if (isFullScreenRef.current) {
      navigate('/ai-assistant');
    }
  }, [navigate]);

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

  /** Trigger the HITL continuation turn after a message's cards resolved. */
  const triggerResume = useCallback(
    async (rowId: string) => {
      const sessionId = currentSessionIdRef.current;
      if (!sessionId) return;
      if (resumedRowIds.current.has(rowId)) return;
      resumedRowIds.current.add(rowId);
      // Race guard: don't fire a resume while another stream is running.
      if (sendingRef.current || busyRef.current) return;
      busyRef.current = true;
      setLiveExtras(EMPTY_EXTRAS);
      transportRef.current?.beginResume();
      await streamSend(SEND_SENTINEL);
    },
    [streamSend],
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
        void triggerResume(row.id);
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
    if (status !== 'error') {
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
    }
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

  // Priority 1: session id from the URL; Priority 2: the store pointer
  // shared with the drawer; base-URL visit on the page → fresh chat.
  useEffect(() => {
    if (initialSessionId) {
      if (initialSessionId !== currentSessionIdRef.current) {
        void loadSession(initialSessionId);
      }
    } else if (!isFullScreen && currentAiSessionId && currentAiSessionId !== currentSessionIdRef.current) {
      void loadSession(currentAiSessionId);
    } else if (currentSessionIdRef.current && isFullScreen && !initialSessionId) {
      setRows([]);
      currentSessionIdRef.current = null;
      setCurrentSessionId(null);
      setIsHistoryOpen(false);
    }
  }, [initialSessionId, isFullScreen, currentAiSessionId, loadSession]);

  const viewMessages = rows.map<ChatMessageView>((row) => ({
    id: row.id,
    role: row.role,
    content: row.content,
    status: 'done',
    meta: { row },
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
