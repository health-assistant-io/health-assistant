import {
  resumeHitlSession,
  streamAIAssistance,
  type AIStreamMessage,
} from '../../services/aiAssistanceService';
import type { TaskInfo, ToolCallInfo } from '../../types/ai';
import type { ChatStreamEvent, ChatStreamTransport } from '../ui/chat';

export function toolChipTitle(name: string): string {
  return name
    .replace(/get_recent_|get_|history/g, '')
    .replace(/_/g, ' ')
    .trim();
}

export interface HealthChatTransportDeps {
  getSessionId: () => string | null;
  /** Contextbag for the next chat turn (patient/examination/entity ids). */
  getContext: () => Record<string, unknown>;
  /** The real user input staged by the provider before `send` (the text
   * argument useChatStream passes is a non-blank sentinel and never reaches
   * the server — image-only turns legitimately carry an empty string). */
  getPendingSend: () => { text: string; images: string[] } | null;
  clearPendingSend: () => void;
  /** Payload events that have no family-event shape (plan 24 V3). */
  onSession: (sessionId: string) => void;
  onCitation: (source: string) => void;
  onTask: (task: TaskInfo) => void;
  /** Localize a classified stream error (errorType code → user-facing text). */
  localizeError: (msg: { error?: string; errorType?: string }) => string;
}

export interface HealthChatTransport extends ChatStreamTransport {
  /** Route the next `send` to the HITL resume endpoint (no user text). */
  beginResume: () => void;
}

/**
 * App-side adapter (family plan 13 H1): maps health's SSE stream — already
 * speaking the family vocabulary natively (plan 24) — onto the library's
 * `ChatStreamEvent` shapes for `useChatStream`. Health's payload events
 * (`session`, `citation`, `task`) bypass the mapping into provider
 * callbacks; a mutable intent routes `send` to the HITL resume endpoint.
 *
 * The stream callbacks resolve when the SSE connection closes, which is not
 * itself an event — a synthetic `flow_finished` terminalizes the turn then
 * (the reducer ignores it when a `flow_failed` frame already terminalized).
 */
export function createChatTransport(deps: HealthChatTransportDeps): HealthChatTransport {
  let onEvent: ((event: ChatStreamEvent) => void) | null = null;
  let intent: { kind: 'send' } | { kind: 'resume' } = { kind: 'send' };
  let sawTerminalFrame = false;

  const emit = (event: ChatStreamEvent) => {
    if (event.event === 'flow_failed' || event.event === 'flow_finished') {
      sawTerminalFrame = true;
    }
    onEvent?.(event);
  };

  const emitFlowEvent = (ev: NonNullable<AIStreamMessage['flowEvent']>) => {
    switch (ev.event) {
      case 'flow_started':
        emit({ event: 'flow_started', flow: ev.flow ?? 'chat' });
        return;
      case 'node_started':
        emit({ event: 'node_started', node: ev.node ?? '' });
        return;
      case 'node_finished':
        emit({
          event: 'node_finished',
          node: ev.node ?? '',
          outcome: ev.outcome === 'failed' ? 'failed' : 'done',
        });
        return;
      case 'interrupt':
        emit({ event: 'interrupt', payload: null });
        return;
      case 'flow_finished':
        emit({ event: 'flow_finished' });
        return;
      case 'flow_failed':
        // Classified failures arrive as `error`/`errorType` on the same
        // frame (the service splits them); handled in handleMessage so the
        // message is localized before it reaches the error state.
        return;
    }
  };

  const handleMessage = (msg: AIStreamMessage) => {
    if (msg.sessionId) {
      deps.onSession(msg.sessionId);
    }
    if (msg.content) {
      emit({ event: 'delta', text: msg.content, kind: 'text' });
    }
    if (msg.flowEvent) {
      emitFlowEvent(msg.flowEvent);
    }
    if (msg.toolCall?.name) {
      const call = msg.toolCall as ToolCallInfo & { result?: string };
      emit({
        event: 'tool_call',
        id: call.name,
        name: call.name,
        title: toolChipTitle(call.name),
        status: call.status === 'finished' ? 'done' : 'running',
        args: call.args,
        result: call.result,
      });
    }
    if (msg.citation) {
      deps.onCitation(msg.citation);
    }
    if (msg.task) {
      deps.onTask(msg.task);
    }
    if (msg.error || msg.errorType) {
      emit({
        event: 'flow_failed',
        code: msg.errorType ?? 'generic',
        message: deps.localizeError(msg),
        retryable: false,
      });
    }
  };

  const onComplete = () => {
    if (!sawTerminalFrame) {
      onEvent?.({ event: 'flow_finished' });
    }
  };

  const onError = () => {
    if (!sawTerminalFrame) {
      emit({
        event: 'flow_failed',
        code: 'connection',
        message: deps.localizeError({ errorType: 'connection' }),
        retryable: false,
      });
    }
  };

  return {
    subscribe: (handlers) => {
      onEvent = handlers.onEvent;
      return () => {
        onEvent = null;
      };
    },
    send: async () => {
      sawTerminalFrame = false;
      const current = intent;
      intent = { kind: 'send' };
      if (current.kind === 'resume') {
        const sessionId = deps.getSessionId();
        if (sessionId === null) {
          throw new Error('No active chat session');
        }
        await resumeHitlSession(sessionId, {}, handleMessage, onComplete, onError);
        return;
      }
      const pending = deps.getPendingSend();
      deps.clearPendingSend();
      await streamAIAssistance(
        {
          task_type: 'chat',
          user_input: pending?.text ?? '',
          images: pending && pending.images.length > 0 ? pending.images : undefined,
          context: { ...deps.getContext(), session_id: deps.getSessionId() },
        },
        handleMessage,
        onComplete,
        onError,
      );
    },
    beginResume: () => {
      intent = { kind: 'resume' };
    },
  };
}
