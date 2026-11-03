import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { streamAIAssistance, resumeHitlSession, AIStreamMessage } from '../aiAssistanceService';

vi.mock('../../api/axios', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  csrfToken: () => 'test-csrf',
}));

/** Build a mocked fetch Response streaming the given SSE frames. */
function sseResponse(frames: object[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

async function collect(
  invoke: (
    onMessage: (m: AIStreamMessage) => void,
    onComplete: () => void,
    onError: (e: any) => void,
  ) => Promise<void>,
) {
  const messages: AIStreamMessage[] = [];
  const onComplete = vi.fn();
  const onError = vi.fn();
  await invoke(
    (m) => messages.push(m),
    onComplete,
    onError,
  );
  return { messages, onComplete, onError };
}

describe('aiAssistanceService family-vocabulary stream (plan 24 V3)', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('consumes family frames onto the component-facing message shapes', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        { event: 'flow_started', flow: 'chat' },
        { event: 'session', session_id: 'sid-1' },
        { event: 'node_started', node: 'agent_step' },
        { event: 'delta', text: 'Hello ' },
        { event: 'delta', text: 'there' },
        { event: 'tool_call', id: 'call_1', name: 'get_patient_summary', status: 'running' },
        {
          event: 'tool_call',
          id: 'call_1',
          name: 'get_patient_summary',
          status: 'done',
          args: '{"patient_id": "p1"}',
          result: 'ok',
        },
        { event: 'citation', source: 'get_patient_summary' },
        {
          event: 'task',
          task: { schema_version: 3, proposal_id: 'prop-1', task_type: 'ask_user', status: 'proposed', proposed_payload: {} },
        },
        { event: 'node_finished', node: 'tool_exec', outcome: 'ok' },
        { event: 'interrupt' },
      ]),
    );

    const { messages, onComplete, onError } = await collect((onMessage, done, err) =>
      streamAIAssistance(
        { task_type: 'chat', user_input: 'hi' },
        onMessage,
        done,
        err,
      ),
    );

    expect(onError).not.toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(messages).toEqual([
      { flowEvent: { event: 'flow_started', flow: 'chat' } },
      { sessionId: 'sid-1' },
      { flowEvent: { event: 'node_started', node: 'agent_step' } },
      { content: 'Hello ' },
      { content: 'there' },
      { toolCall: { name: 'get_patient_summary', status: 'executing', args: undefined, result: undefined } },
      {
        toolCall: {
          name: 'get_patient_summary',
          status: 'finished',
          args: '{"patient_id": "p1"}',
          result: 'ok',
        },
      },
      { citation: 'get_patient_summary' },
      { task: { schema_version: 3, proposal_id: 'prop-1', task_type: 'ask_user', status: 'proposed', proposed_payload: {} } },
      { flowEvent: { event: 'node_finished', node: 'tool_exec', outcome: 'ok' } },
      { flowEvent: { event: 'interrupt' } },
    ]);
  });

  it('maps the classified flow_failed frame to the localized-error path and stops consuming', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"event": "flow_started", "flow": "chat"}\n\n'));
        controller.enqueue(
          encoder.encode(
            'data: {"event": "flow_failed", "retryable": false, "code": "guard", "message": "Chat session not found."}\n\n',
          ),
        );
        // Would-be-subsequent frames must never surface after the terminal one.
        controller.enqueue(encoder.encode('data: {"event": "delta", "text": "TOO LATE"}\n\n'));
        controller.close();
      },
    });
    fetchMock.mockResolvedValue(new Response(stream, { status: 200 }));

    const { messages, onComplete, onError } = await collect((onMessage, done, err) =>
      streamAIAssistance({ task_type: 'chat', user_input: 'hi' }, onMessage, done, err),
    );

    expect(onError).not.toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(messages).toEqual([
      { flowEvent: { event: 'flow_started', flow: 'chat' } },
      { flowEvent: { event: 'flow_failed', retryable: false, code: 'guard', message: 'Chat session not found.' } },
      { error: 'Chat session not found.', errorType: 'guard' },
    ]);
    expect(messages.some((m) => m.content === 'TOO LATE')).toBe(false);
  });

  it('ignores unknown events (additive family vocabulary stays safe)', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([
        { event: 'delta', text: 'ok' },
        { event: 'some_future_event', text: 'nope' },
        { event: 'flow_finished' },
      ]),
    );

    const { messages, onComplete, onError } = await collect((onMessage, done, err) =>
      streamAIAssistance({ task_type: 'chat', user_input: 'hi' }, onMessage, done, err),
    );

    expect(onError).not.toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(messages).toEqual([
      { content: 'ok' },
      { flowEvent: { event: 'flow_finished' } },
    ]);
  });

  it('drops malformed task payloads with a warning instead of rendering them', async () => {
    fetchMock.mockResolvedValue(
      sseResponse([{ event: 'task', task: { proposal_id: 'x' } }]),
    );
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => { /* quiet */ });
    try {
      const { messages } = await collect((onMessage, done, err) =>
        streamAIAssistance({ task_type: 'chat', user_input: 'hi' }, onMessage, done, err),
      );
      expect(messages).toEqual([]);
      expect(warn).toHaveBeenCalled();
    } finally {
      warn.mockRestore();
    }
  });

  it('fetches the stream without the removed flow_events gate', async () => {
    fetchMock.mockResolvedValue(sseResponse([{ event: 'flow_finished' }]));

    await collect((onMessage, done, err) =>
      streamAIAssistance({ task_type: 'chat', user_input: 'hi' }, onMessage, done, err),
    );
    const streamUrl = fetchMock.mock.calls[0][0] as string;
    expect(streamUrl).toBe('/api/v1/ai-assistance/stream');
    expect(streamUrl).not.toContain('flow_events');

    await collect((onMessage, done, err) =>
      resumeHitlSession('sid-1', undefined, onMessage, done, err),
    );
    const resumeUrl = fetchMock.mock.calls[1][0] as string;
    expect(resumeUrl).toBe('/api/v1/ai-assistance/sessions/sid-1/resume');
    expect(resumeUrl).not.toContain('flow_events');
  });
});
