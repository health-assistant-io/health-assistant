import assert from "node:assert/strict";
import test from "node:test";
import {
  BridgeException,
  HealthAssistantBridgeClient,
  signRequest,
} from "../src/index.ts";

const BASE = "https://example.test";
const IID = "00000000-0000-0000-0000-000000000000";
const SECRET = "a-very-long-random-secret";

interface CapturedCall {
  url: string | URL | Request;
  method?: string;
  headers: Record<string, string>;
  body?: unknown;
}

/** Replace global fetch for the duration of `fn`, capturing every call. */
async function withMockedFetch(
  respond: (call: CapturedCall) => Response,
  fn: (calls: CapturedCall[]) => Promise<void>,
): Promise<void> {
  const realFetch = globalThis.fetch;
  const calls: CapturedCall[] = [];
  globalThis.fetch = (async (
    url: string | URL | Request,
    init?: RequestInit,
  ) => {
    const call: CapturedCall = {
      url,
      method: init?.method,
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body,
    };
    calls.push(call);
    return respond(call);
  }) as typeof fetch;
  try {
    await fn(calls);
  } finally {
    globalThis.fetch = realFetch;
  }
}

function sseResponse(chunks: string[], statusCode = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: statusCode,
    headers: { "content-type": "text/event-stream" },
  });
}

function erroringSseResponse(firstChunk: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(firstChunk));
    },
    pull(controller) {
      controller.error(new Error("connection reset mid-body"));
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

async function collect(gen: AsyncGenerator<string>): Promise<string[]> {
  const lines: string[] = [];
  for await (const line of gen) {
    lines.push(line);
  }
  return lines;
}

test("requestLines yields raw lines split across chunk boundaries", async () => {
  await withMockedFetch(
    () =>
      sseResponse([
        'data: {"event":"session","session_i',
        'd":"s1"}\n\ndata: {"event":"delta","text":"He',
        'llo"}\n\n: keep-alive\n\ndata: {"event":"flow_finished"}\n\n',
      ]),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const lines = await collect(
        client.requestLines(
          "POST",
          "/ai/chat/stream",
          Buffer.from("{}", "utf8"),
        ),
      );
      assert.deepEqual(lines, [
        'data: {"event":"session","session_id":"s1"}',
        "",
        'data: {"event":"delta","text":"Hello"}',
        "",
        ": keep-alive",
        "",
        'data: {"event":"flow_finished"}',
        "",
      ]);
      assert.equal(calls.length, 1);
    },
  );
});

test("requestLines yields a final unterminated line and strips CR", async () => {
  await withMockedFetch(
    () => sseResponse(["data: one\r\n\r\ndata: two"]),
    async () => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const lines = await collect(client.requestLines("POST", "/ai/chat/stream"));
      assert.deepEqual(lines, ["data: one", "", "data: two"]);
    },
  );
});

test("requestLines throws BridgeException before the first emission on non-2xx", async () => {
  await withMockedFetch(
    () => new Response('{"detail":"nope"}', { status: 400 }),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const lines: string[] = [];
      await assert.rejects(
        collect(client.requestLines("POST", "/ai/sessions/s-1/resume")),
        (err: unknown) => {
          assert.ok(err instanceof BridgeException);
          assert.equal(err.statusCode, 400);
          assert.match(err.body, /nope/);
          return true;
        },
      );
      assert.deepEqual(lines, []);
      assert.equal(calls.length, 1);
    },
  );
});

test("a mid-body stream error surfaces without a second request (no retry)", async () => {
  const realFetch = globalThis.fetch;
  let callsCount = 0;
  globalThis.fetch = (async () => {
    callsCount++;
    return erroringSseResponse('data: {"event":"delta","text":"partial"}\n');
  }) as typeof fetch;
  try {
    const client = new HealthAssistantBridgeClient(BASE, IID, {
      apiSecret: SECRET,
    });
    const lines: string[] = [];
    await assert.rejects(
      (async () => {
        for await (const line of client.requestLines(
          "POST",
          "/ai/chat/stream",
          Buffer.from("{}", "utf8"),
        )) {
          lines.push(line);
        }
      })(),
    );
    assert.deepEqual(lines, ['data: {"event":"delta","text":"partial"}']);
    assert.equal(callsCount, 1);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("chatStream never retries a failed dispatch (fetch rejects → exactly one call)", async () => {
  const realFetch = globalThis.fetch;
  let callsCount = 0;
  globalThis.fetch = (async () => {
    callsCount++;
    throw new TypeError("fetch failed");
  }) as typeof fetch;
  try {
    const client = new HealthAssistantBridgeClient(BASE, IID, {
      apiSecret: SECRET,
    });
    await assert.rejects(() => collect(client.chatStream("Hello")));
    // A replayed turn POST would duplicate the turn — exactly one request.
    assert.equal(callsCount, 1);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("chatStream never retries a 503 stream turn (BridgeException, one call)", async () => {
  await withMockedFetch(
    () => new Response('{"detail":"down"}', { status: 503 }),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      await assert.rejects(
        collect(client.chatStream("Hello")),
        (err: unknown) => {
          assert.ok(err instanceof BridgeException);
          assert.equal((err as BridgeException).statusCode, 503);
          return true;
        },
      );
      assert.equal(calls.length, 1);
    },
  );
});

test("chatStream signs POST /ai/chat/stream and threads the session id", async () => {
  await withMockedFetch(
    () => sseResponse(['data: {"event":"delta","text":"Hi"}\n\n']),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const lines = await collect(client.chatStream("Summarize my lipids", "s-1"));
      assert.deepEqual(lines, ['data: {"event":"delta","text":"Hi"}', ""]);

      const call = calls[0];
      assert.equal(call.method, "POST");
      assert.equal(
        call.url,
        `${BASE}/api/v1/integrations/health_assistant_bridge/api/${IID}/ai/chat/stream`,
      );
      const body = Buffer.from(call.body as Uint8Array);
      assert.deepEqual(JSON.parse(body.toString("utf8")), {
        message: "Summarize my lipids",
        session_id: "s-1",
      });
      assert.equal(
        call.headers["X-Api-Signature"],
        signRequest(
          SECRET,
          "POST",
          "/ai/chat/stream",
          body,
          Number(call.headers["X-Api-Timestamp"]),
        )["X-Api-Signature"],
      );
    },
  );
});

test("resumeChatStream signs the resume turn and omits message_id when absent", async () => {
  await withMockedFetch(
    () => sseResponse(['data: {"event":"flow_finished"}\n\n']),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const lines = await collect(client.resumeChatStream("s-1"));
      assert.deepEqual(lines, ['data: {"event":"flow_finished"}', ""]);

      const call = calls[0];
      assert.equal(call.method, "POST");
      assert.equal(
        call.url,
        `${BASE}/api/v1/integrations/health_assistant_bridge/api/${IID}/ai/sessions/s-1/resume`,
      );
      const body = Buffer.from(call.body as Uint8Array);
      assert.deepEqual(JSON.parse(body.toString("utf8")), {});
      assert.equal(
        call.headers["X-Api-Signature"],
        signRequest(
          SECRET,
          "POST",
          "/ai/sessions/s-1/resume",
          body,
          Number(call.headers["X-Api-Timestamp"]),
        )["X-Api-Signature"],
      );
    },
  );
});

test("resumeChatStream threads message_id into the signed body", async () => {
  await withMockedFetch(
    () => sseResponse(['data: {"event":"delta","text":"Ack"}\n\n']),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      await collect(client.resumeChatStream("s-1", "m-1"));
      const body = Buffer.from(calls[0].body as Uint8Array);
      assert.deepEqual(JSON.parse(body.toString("utf8")), { message_id: "m-1" });
    },
  );
});
