import assert from "node:assert/strict";
import test from "node:test";
import {
  HealthAssistantBridgeClient,
  SDK_VERSION,
  signRequest,
} from "../src/index.ts";

const BASE = "https://example.test";
const IID = "00000000-0000-0000-0000-000000000000";
const STATUS_URL =
  `${BASE}/api/v1/integrations/health_assistant_bridge/api/${IID}/status`;

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

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

test("getStatus sends X-Api-Signature + X-Api-Timestamp when apiSecret is set", async () => {
  const secret = "a-very-long-random-secret";
  await withMockedFetch(
    () => jsonResponse({ status: "active" }),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: secret,
      });
      await client.getStatus();

      assert.equal(calls.length, 1);
      const call = calls[0];
      assert.equal(call.url, STATUS_URL);
      assert.equal(call.method, "GET");
      const ts = call.headers["X-Api-Timestamp"];
      assert.ok(ts, "X-Api-Timestamp header present");
      assert.match(ts, /^\d+$/);
      const expected = signRequest(
        secret,
        "GET",
        "/status",
        Buffer.alloc(0),
        Number(ts),
      );
      assert.equal(call.headers["X-Api-Signature"], expected["X-Api-Signature"]);
      assert.equal(
        call.body,
        undefined,
        "signed GET must not carry a body (empty Buffer signs, but is not sent)",
      );
    },
  );
});

test("getStatus sends no signature headers without apiSecret", async () => {
  await withMockedFetch(
    () => jsonResponse({ status: "active", server_time: 1760000000 }),
    async (calls) => {
      const client = new HealthAssistantBridgeClient(BASE, IID);
      await client.getStatus();

      assert.equal(calls.length, 1);
      const headers = calls[0].headers;
      assert.equal(headers["X-Api-Signature"], undefined);
      assert.equal(headers["X-Api-Timestamp"], undefined);
      assert.equal(calls[0].body, undefined);
    },
  );
});

test("getStatus decodes the minimal unsigned shape", async () => {
  await withMockedFetch(
    () => jsonResponse({ status: "active", server_time: 1760000000 }),
    async () => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: "a-very-long-random-secret",
      });
      const status = await client.getStatus();
      assert.equal(status.status, "active");
      assert.equal(status.server_time, 1760000000);
      assert.equal(status.cursor, undefined);
      assert.equal(status.server_version, undefined);
      assert.deepEqual(status.bridge_features, undefined);
    },
  );
});

test("getStatus decodes the full signed payload shape", async () => {
  await withMockedFetch(
    () =>
      jsonResponse({
        status: "active",
        server_time: 1760000000,
        integration_id: IID,
        last_synced_at: null,
        cursor: "2026-07-24T12:00:00Z",
        latest_sdks: { python: "1.3.0", ts: SDK_VERSION, kotlin: "0.5.0" },
        frontend_base_url: "http://localhost:3000",
        server_version: "1.2.0",
        bridge_features: ["exam_enrichment", "ai_assistant", "ai_streaming"],
      }),
    async () => {
      const client = new HealthAssistantBridgeClient(BASE, IID, {
        apiSecret: "a-very-long-random-secret",
      });
      const status = await client.getStatus();
      assert.equal(status.status, "active");
      assert.equal(status.server_time, 1760000000);
      assert.equal(status.integration_id, IID);
      assert.equal(status.cursor, "2026-07-24T12:00:00Z");
      assert.equal(status.latest_sdks?.kotlin, "0.5.0");
      assert.equal(status.frontend_base_url, "http://localhost:3000");
      assert.equal(status.server_version, "1.2.0");
      assert.deepEqual(status.bridge_features, [
        "exam_enrichment",
        "ai_assistant",
        "ai_streaming",
      ]);
    },
  );
});
