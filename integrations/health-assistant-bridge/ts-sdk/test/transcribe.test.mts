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
const TRANSCRIBE_URL =
  `${BASE}/api/v1/integrations/health_assistant_bridge/api/${IID}/ai/transcribe`;

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

test("transcribe signs the base64 body and decodes the reply", async () => {
  const audio = Buffer.from([0x01, 0x02, 0x03]);
  await withMockedFetch(
    () =>
      new Response(JSON.stringify({ text: "Hello from voice.", success: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      const result = await client.transcribe(audio, "audio/webm;codecs=opus");
      assert.equal(result.text, "Hello from voice.");
      assert.equal(result.success, true);

      assert.equal(calls.length, 1);
      const call = calls[0];
      assert.equal(call.method, "POST");
      assert.equal(call.url, TRANSCRIBE_URL);
      const body = Buffer.from(call.body as Uint8Array);
      assert.deepEqual(JSON.parse(body.toString("utf8")), {
        data: audio.toString("base64"),
        mime: "audio/webm;codecs=opus",
      });
      assert.equal(
        call.headers["X-Api-Signature"],
        signRequest(
          SECRET,
          "POST",
          "/ai/transcribe",
          body,
          Number(call.headers["X-Api-Timestamp"]),
        )["X-Api-Signature"],
      );
    },
  );
});

test("transcribe surfaces the typed 400 validation guard", async () => {
  await withMockedFetch(
    () =>
      new Response(
        JSON.stringify({
          detail: {
            code: "validation",
            message: "Unsupported audio format. Use WebM/Opus, OGG, MP4, or WAV.",
          },
        }),
        { status: 400 },
      ),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: SECRET,
      });
      await assert.rejects(
        client.transcribe(Buffer.from([0x01]), "video/webm"),
        (err: unknown) => {
          assert.ok(err instanceof BridgeException);
          assert.equal(err.statusCode, 400);
          assert.equal(err.code, "validation");
          assert.match(err.body, /Unsupported audio format/);
          return true;
        },
      );
      // A 400 is not retryable — exactly one dispatch.
      assert.equal(calls.length, 1);
    },
  );
});
