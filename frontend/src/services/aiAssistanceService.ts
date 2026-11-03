import api, { csrfToken } from '../api/axios';
import { TaskInfo } from '../types/ai';


/**
 * §10 (plan 16 H3): raw-fetch auth init — the HttpOnly cookie session rides
 * `credentials: 'include'`, and non-safe requests echo the readable
 * `nx_csrf` cookie (double submit). No Authorization header from storage.
 */
function sessionFetchHeaders(): Record<string, string> {
  const csrf = csrfToken();
  return csrf ? { 'X-CSRF-Token': csrf } : {};
}

export interface AIAssistanceRequest {
  task_type: 'fill_biomarker_form' | 'fill_medication_form' | 'define_biomarker' | 'define_medication' | 'define_anatomy_graph' | 'chat' | 'magic_fill_examination' | 'suggest_category_icon' | 'generate_category_icon';
  user_input: string;
  reference_image?: string;
  /** Image attachments (RFC 2397 data URLs) for multimodal chat. */
  images?: string[];
  context?: Record<string, any>;
}

export interface AIAssistanceResponse {
  task_type: string;
  suggested_data?: Record<string, any>;
  suggested_icons?: string[];
  svg_content?: string;
  justification?: string;
  message?: string;
  session_id?: string;
  success: boolean;
  error?: string;
}

export interface ChatSession {
  id: string;
  title?: string;
  patient_id?: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: { text: string; images?: string[] };
  tool_calls?: any[];
  citations?: string[];
  tasks?: TaskInfo[];
  created_at: string;
  /** Branch decoration (family contract, plan 13 H2). */
  parent_id?: string | null;
  variant_index?: number;
  variant_count?: number;
  sibling_ids?: string[];
}

/** One node of the read-only branch-tree projection (plan 13 H2). */
export interface ChatBranchNode {
  id: string;
  role: 'user' | 'assistant';
  excerpt: string;
  parent_id: string | null;
  children: string[];
  active_child_id: string | null;
}

export interface ChatTree {
  active_root_id: string | null;
  nodes: ChatBranchNode[];
}

export const getAIAssistance = async (request: AIAssistanceRequest): Promise<AIAssistanceResponse> => {
  const response = await api.post<AIAssistanceResponse>('/ai-assistance/assist', request);
  return response.data;
};

export const listChatSessions = async (patientId?: string): Promise<ChatSession[]> => {
  const response = await api.get<ChatSession[]>('/ai-assistance/sessions', {
    params: { patient_id: patientId }
  });
  return response.data;
};

export const getChatSessionMessages = async (sessionId: string): Promise<ChatMessage[]> => {
  const response = await api.get<ChatMessage[]>(`/ai-assistance/sessions/${sessionId}/messages`);
  return response.data;
};

export const deleteChatSession = async (sessionId: string): Promise<void> => {
  await api.delete(`/ai-assistance/sessions/${sessionId}`);
};

export const renameChatSession = async (sessionId: string, title: string): Promise<ChatSession> => {
  const response = await api.patch<ChatSession>(`/ai-assistance/sessions/${sessionId}`, { title });
  return response.data;
};

export interface HitlResolution {
  status: 'confirmed' | 'dismissed';
  final_payload?: Record<string, any>;
  result?: Record<string, any>;
  error?: string;
}

export const resolveHitlTask = async (
  sessionId: string,
  proposalId: string,
  resolution: HitlResolution
): Promise<{ success: boolean; task: TaskInfo }> => {
  const response = await api.post(
    `/ai-assistance/sessions/${sessionId}/tasks/${proposalId}/resolve`,
    resolution
  );
  return response.data;
};

/**
 * Trigger an agent continuation turn after the user has resolved one or more
 * HITL task cards in the session. Returns an SSE stream parsed identically to
 * `streamAIAssistance`. The `messageId` selector is optional — when omitted
 * the server resumes from the most recent task-bearing message.
 *
 * Security note: outcomes are read from the session's tasks JSONB on the
 * server; this call only carries selectors.
 */
export const resumeHitlSession = async (
  sessionId: string,
  params: { messageId?: string } | undefined,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
) => {
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

  try {
    const response = await fetch(`${API_BASE_URL}/ai-assistance/sessions/${sessionId}/resume`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...sessionFetchHeaders(),
      },
      body: JSON.stringify(params && params.messageId ? { message_id: params.messageId } : {})
    });

    await consumeAIStreamResponse(response, onMessage, onComplete, onError);
  } catch (error) {
    onError(error);
  }
};

/**
 * Edit-and-resend as a branch (family contract, plan 13 H2): the edited
 * text re-sends as a new sibling variant of the target user message and a
 * fresh reply streams (same SSE contract as /stream). `context` mirrors
 * /stream's (patient/examination bindings for tools).
 */
export const editChatMessageStream = async (
  messageId: string,
  content: string,
  context: Record<string, unknown>,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
) => {
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

  try {
    const response = await fetch(`${API_BASE_URL}/ai-assistance/messages/${messageId}/edit`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...sessionFetchHeaders(),
      },
      body: JSON.stringify({ content, context }),
    });

    await consumeAIStreamResponse(response, onMessage, onComplete, onError);
  } catch (error) {
    onError(error);
  }
};

/**
 * Regenerate as a branch (family contract, plan 13 H2): stream a fresh
 * assistant sibling under the target user message — earlier replies stay
 * switchable variants, never overwritten.
 */
export const regenerateChatMessageStream = async (
  messageId: string,
  context: Record<string, unknown>,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
) => {
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

  try {
    const response = await fetch(
      `${API_BASE_URL}/ai-assistance/messages/${messageId}/regenerate`,
      {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...sessionFetchHeaders(),
        },
        body: JSON.stringify({ context }),
      }
    );

    await consumeAIStreamResponse(response, onMessage, onComplete, onError);
  } catch (error) {
    onError(error);
  }
};

/** Flip one branch pointer (level-flip semantics); returns the re-walked
 * visible path with variant decoration (plan 13 H2). */
export const selectChatMessage = async (messageId: string): Promise<ChatMessage[]> => {
  const response = await api.post<ChatMessage[]>(`/ai-assistance/messages/${messageId}/select`);
  return response.data;
};

/** Read-only branch-tree projection for the tree rail (plan 13 H2). */
export const getChatTree = async (sessionId: string): Promise<ChatTree> => {
  const response = await api.get<ChatTree>(`/ai-assistance/sessions/${sessionId}/tree`);
  return response.data;
};

/**
 * Family chat vocabulary (ai-features §5) + health's payload events
 * (plan 24 V3): every SSE frame is one JSON object with the event name in
 * `event`. Family core: `flow_started` / `node_started` / `node_finished` /
 * `delta` / `tool_call` / `flow_finished` / `flow_failed`. Health payload
 * events: `citation` (a data-source tool name), `task` (an interactive HITL
 * card, payload identical to the persisted tasks JSONB), `interrupt` (the
 * resumable ask_user pause — NOT the terminal family `flow_interrupted`,
 * which health does not emit), and `session` (a freshly auto-created chat
 * session id). Unknown events are ignored (additive family growth is safe).
 */
export interface AIFlowEvent {
  event: 'flow_started' | 'node_started' | 'node_finished' | 'interrupt' | 'flow_finished' | 'flow_failed';
  flow?: string;
  node?: string;
  outcome?: string;
}

export interface AIStreamMessage {
  content?: string;
  flowEvent?: AIFlowEvent;
  sessionId?: string;
  toolCall?: {
    name: string;
    status: 'starting' | 'executing' | 'finished';
    args?: string;
    result?: string;
  };
  citation?: string;
  task?: TaskInfo;
  error?: string;
  errorType?: string;
}

/**
 * Parse one family-frame SSE stream. `onComplete` fires exactly once — on
 * clean stream end OR after a terminal `flow_failed` frame (the chat UI
 * resets its loading state only in the onComplete/onError callbacks; a bare
 * return would leave it spinning forever after a connection error).
 */
async function consumeAIStreamResponse(
  response: Response,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
): Promise<void> {
  if (!response.ok) {
    // Pre-stream rejections (e.g. the per-session turn lock's 409) carry a
    // user-facing `detail` — surface it through the guard channel so the
    // error tile shows the server's message, not a bare HTTP code.
    let detail = `Stream failed (HTTP ${response.status})`;
    try {
      const errBody = await response.json();
      if (errBody?.detail) detail = errBody.detail;
    } catch {
      /* ignore parse error */
    }
    onMessage({ error: detail, errorType: 'guard' });
    onComplete();
    return;
  }

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  if (!reader) throw new Error('No reader available');

  try {
    let buffer = '';
    let terminal = false;
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // Keep the last partial line in the buffer
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.trim().startsWith('data: ')) {
          const content = line.trim().replace('data: ', '');
          try {
            const data = JSON.parse(content);
            if (handleAIStreamEvent(data, onMessage, onComplete)) {
              // Terminal classified failure — stop consuming (the chat UI
              // resets its loading state in the onComplete callback).
              terminal = true;
              break;
            }
          } catch (e) {
            console.warn('Failed to parse SSE chunk', e);
          }
        }
      }
    }
    if (terminal) {
      // Terminal classified failure — close the connection; onComplete was
      // already called by the frame handler (exactly once).
      reader.cancel().catch(() => { /* stream already closed */ });
      return;
    }
    onComplete();
  } catch (error) {
    onError(error);
  }
}

/**
 * Map one parsed family frame onto the component-facing AIStreamMessage
 * shapes. Returns true when the frame was terminal (a classified
 * `flow_failed`) and the caller should stop consuming.
 */
function handleAIStreamEvent(
  data: any,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void
): boolean {
  switch (data.event) {
    case 'session':
      onMessage({ sessionId: data.session_id });
      return false;
    case 'delta':
      if (data.text) {
        onMessage({ content: data.text });
      }
      return false;
    case 'tool_call': {
      // §5 statuses (running | done) onto the component's tool-chip states.
      if (data.name) {
        onMessage({
          toolCall: {
            name: data.name,
            status: data.status === 'done' ? 'finished' : 'executing',
            args: typeof data.args === 'string' ? data.args : data.args ? JSON.stringify(data.args) : undefined,
            result: data.result
          }
        });
      }
      return false;
    }
    case 'citation':
      onMessage({ citation: data.source });
      return false;
    case 'task': {
      const task = data.task as TaskInfo;
      if (task && task.proposal_id && task.task_type) {
        onMessage({ task });
      } else {
        console.warn('Malformed task payload (missing proposal_id/task_type)', task);
      }
      return false;
    }
    case 'flow_started':
    case 'node_started':
    case 'node_finished':
    case 'interrupt':
    case 'flow_finished':
      onMessage({ flowEvent: data });
      return false;
    case 'flow_failed':
      onMessage({ flowEvent: data });
      if (data.code) {
        // Terminal classified failure (the endpoint's error frame): the
        // backend maps LLM/provider errors to a stable ``code``
        // (connection/auth/rate_limit/timeout/model_config/generic) and
        // leaves ``message`` empty so no raw SDK text leaks; soft guard
        // violations carry code "guard" + the user-facing message.
        onMessage({ error: data.message, errorType: data.code });
        onComplete();
        return true;
      }
      return false;
    default:
      // Unknown events are ignored — additive family vocabulary stays safe.
      return false;
  }
}

export const streamAIAssistance = async (
  request: AIAssistanceRequest,
  onMessage: (msg: AIStreamMessage) => void,
  onComplete: () => void,
  onError: (error: any) => void
) => {
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

  try {
    const response = await fetch(`${API_BASE_URL}/ai-assistance/stream`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...sessionFetchHeaders(),
      },
      body: JSON.stringify(request)
    });

    await consumeAIStreamResponse(response, onMessage, onComplete, onError);
  } catch (error) {
    onError(error);
  }
};

export interface AIToolInfo {
  name: string;
  description: string;
  source: string;
  schema?: Record<string, any>;
}

export const getAvailableTools = async (patientId: string, examinationId?: string): Promise<AIToolInfo[]> => {
  const params = new URLSearchParams();
  params.append('patient_id', patientId);
  if (examinationId) params.append('examination_id', examinationId);
  
  const response = await api.get<AIToolInfo[]>(`/ai-assistance/tools?${params.toString()}`);
  return response.data;
};

export interface TranscriptionResult {
  text: string;
  success: boolean;
}

/**
 * Transcribe a compressed voice recording to text (speech-to-text).
 *
 * Sends the audio as multipart/form-data to ``/ai-assistance/transcribe``.
 * The audio is ephemeral: the backend discards it after transcription (it may
 * contain PHI). The returned text is placed into the chat input box for the
 * user to review/edit before sending — it then flows through the normal chat
 * pipeline (prompt guard + HITL).
 */
export const transcribeAudio = async (
  blob: Blob,
  filename: string = 'recording.webm',
): Promise<TranscriptionResult> => {
  const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';
  const form = new FormData();
  form.append('file', blob, filename);

  const response = await fetch(`${API_BASE_URL}/ai-assistance/transcribe`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      // Do NOT set Content-Type — the browser sets the multipart boundary.
      ...sessionFetchHeaders(),
    },
    body: form,
  });

  if (!response.ok) {
    let detail = `Transcription failed (HTTP ${response.status})`;
    try {
      const errBody = await response.json();
      if (errBody?.detail) detail = errBody.detail;
    } catch {
      /* ignore parse error */
    }
    throw new Error(detail);
  }
  return response.json();
};
