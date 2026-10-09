import { describe, expect, it, vi } from 'vitest';
import type { ChatStreamEvent } from '../../ui/chat';
import {
  createChatTransport,
  type HealthChatTransportDeps,
} from '../chatTransport';
import type { AIStreamMessage } from '../../../services/aiAssistanceService';

const mockStream = vi.hoisted(() => vi.fn());
const mockResume = vi.hoisted(() => vi.fn());

vi.mock('../../../services/aiAssistanceService', () => ({
  streamAIAssistance: mockStream,
  resumeHitlSession: mockResume,
}));

type Captured = {
  onMessage: (msg: AIStreamMessage) => void;
  onComplete: () => void;
  onError: (error: unknown) => void;
};

function capture(): Captured {
  const captured = {} as Captured;
  mockStream.mockImplementationOnce(
    async (
      _request: unknown,
      onMessage: Captured['onMessage'],
      onComplete: Captured['onComplete'],
      onError: Captured['onError'],
    ) => {
      captured.onMessage = onMessage;
      captured.onComplete = onComplete;
      captured.onError = onError;
    },
  );
  return captured;
}

function makeDeps(overrides: Partial<HealthChatTransportDeps> = {}): HealthChatTransportDeps {
  return {
    getSessionId: () => 'session-1',
    getContext: () => ({ patient_id: 'p1' }),
    getPendingSend: () => ({ text: 'hello', images: [] }),
    clearPendingSend: () => {},
    onSession: () => {},
    onCitation: () => {},
    onTask: () => {},
    localizeError: (msg) => msg.errorType ?? msg.error ?? 'generic',
    ...overrides,
  };
}

function collect(transport: ReturnType<typeof createChatTransport>): ChatStreamEvent[] {
  const events: ChatStreamEvent[] = [];
  transport.subscribe({ onEvent: (event) => events.push(event) });
  return events;
}

describe('createChatTransport', () => {
  it('maps family flow events and deltas onto ChatStreamEvent', async () => {
    const deps = makeDeps();
    const transport = createChatTransport(deps);
    const events = collect(transport);
    const captured = capture();

    await transport.send({ text: 'x' });
    expect(mockStream).toHaveBeenCalledTimes(1);

    captured.onMessage({ flowEvent: { event: 'flow_started', flow: 'chat' } });
    captured.onMessage({ flowEvent: { event: 'node_started', node: 'agent_step' } });
    captured.onMessage({ content: 'Hi' });
    captured.onMessage({ flowEvent: { event: 'node_finished', node: 'tool_exec', outcome: 'done' } });
    captured.onMessage({ flowEvent: { event: 'flow_finished' } });
    captured.onComplete();

    expect(events.map((e) => e.event)).toEqual([
      'flow_started',
      'node_started',
      'delta',
      'node_finished',
      'flow_finished',
    ]);
    expect(events[2]).toEqual({ event: 'delta', text: 'Hi', kind: 'text' });
  });

  it('maps tool_call frames keyed by name with chip titles', async () => {
    const transport = createChatTransport(makeDeps());
    const events = collect(transport);
    const captured = capture();
    await transport.send({ text: 'x' });

    captured.onMessage({ toolCall: { name: 'get_recent_observations', status: 'executing' } });
    captured.onMessage({
      toolCall: { name: 'get_recent_observations', status: 'finished', result: '[1,2]' },
    });

    expect(events).toEqual([
      {
        event: 'tool_call',
        id: 'get_recent_observations',
        name: 'get_recent_observations',
        title: 'observations',
        status: 'running',
        args: undefined,
        result: undefined,
      },
      {
        event: 'tool_call',
        id: 'get_recent_observations',
        name: 'get_recent_observations',
        title: 'observations',
        status: 'done',
        args: undefined,
        result: '[1,2]',
      },
    ]);
  });

  it('routes payload events to provider callbacks, not the stream', async () => {
    const onSession = vi.fn();
    const onCitation = vi.fn();
    const onTask = vi.fn();
    const transport = createChatTransport(makeDeps({ onSession, onCitation, onTask }));
    const events = collect(transport);
    const captured = capture();
    await transport.send({ text: 'x' });

    captured.onMessage({ sessionId: 'session-2' });
    captured.onMessage({ citation: 'get_lab_results' });
    captured.onMessage({ task: { proposal_id: 'p1', task_type: 'ask_user' } as never });

    expect(onSession).toHaveBeenCalledWith('session-2');
    expect(onCitation).toHaveBeenCalledWith('get_lab_results');
    expect(onTask).toHaveBeenCalled();
    expect(events).toHaveLength(0);
  });

  it('maps classified errors onto a localized terminal flow_failed', async () => {
    const localizeError = vi.fn(() => 'localized error');
    const transport = createChatTransport(makeDeps({ localizeError }));
    const events = collect(transport);
    const captured = capture();
    await transport.send({ text: 'x' });

    captured.onMessage({ flowEvent: { event: 'flow_failed' } });
    captured.onMessage({ error: '', errorType: 'connection' });
    captured.onComplete();

    expect(localizeError).toHaveBeenCalledWith({ error: '', errorType: 'connection' });
    expect(events).toEqual([
      {
        event: 'flow_failed',
        code: 'connection',
        message: 'localized error',
        retryable: false,
      },
    ]);
    // flow_failed already terminalized — no synthetic flow_finished after it
    expect(events.filter((e) => e.event === 'flow_finished')).toHaveLength(0);
  });

  it('emits a synthetic flow_finished when the stream ends without one', async () => {
    const transport = createChatTransport(makeDeps());
    const events = collect(transport);
    const captured = capture();
    await transport.send({ text: 'x' });

    captured.onMessage({ content: 'partial' });
    captured.onComplete();

    expect(events).toEqual([
      { event: 'delta', text: 'partial', kind: 'text' },
      { event: 'flow_finished' },
    ]);
  });

  it('emits a terminal flow_failed when the stream errors', async () => {
    const transport = createChatTransport(makeDeps());
    const events = collect(transport);
    const captured = capture();
    await transport.send({ text: 'x' });

    captured.onError(new Error('network down'));

    expect(events).toEqual([
      {
        event: 'flow_failed',
        code: 'connection',
        message: 'connection',
        retryable: false,
      },
    ]);
  });

  it('passes the pending send text/images and session context to the stream', async () => {
    const getContext = () => ({ patient_id: 'p1', current_tab: 'chat' });
    const getPendingSend = () => ({ text: 'what is this?', images: ['data:image/png;base64,x'] });
    const transport = createChatTransport(makeDeps({ getContext, getPendingSend }));
    capture();

    await transport.send({ text: '‍' });

    expect(mockStream).toHaveBeenCalledWith(
      {
        task_type: 'chat',
        user_input: 'what is this?',
        images: ['data:image/png;base64,x'],
        context: { patient_id: 'p1', current_tab: 'chat', session_id: 'session-1' },
      },
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    );
  });

  it('routes the next send to the HITL resume endpoint via beginResume', async () => {
    const transport = createChatTransport(makeDeps());
    const captured = {} as Captured;
    mockResume.mockImplementationOnce(
      async (
        _id: string,
        _params: unknown,
        onMessage: Captured['onMessage'],
        onComplete: Captured['onComplete'],
      ) => {
        captured.onMessage = onMessage;
        captured.onComplete = onComplete;
      },
    );

    transport.beginResume();
    await transport.send({ text: '‍' });

    expect(mockResume).toHaveBeenCalledWith('session-1', {}, expect.any(Function), expect.any(Function), expect.any(Function));
    expect(mockStream).not.toHaveBeenCalled();

    captured.onMessage({ content: 'continuing' });
    captured.onComplete();
    const events = collect(transport);
    expect(events).toEqual([]);
  });
});
