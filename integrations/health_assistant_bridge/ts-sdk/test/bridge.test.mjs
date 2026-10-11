// node:test harness for the TS SDK — mocked global fetch, no server.
// Requires Node >= 23.6 (native TS type stripping for the ../src import).
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  HealthAssistantBridgeClient,
  signRequest,
} from "../src/index.ts";

const BASE = "https://example.test";
const INTEGRATION = "00000000-0000-0000-0000-000000000000";
const API_ROOT = `${BASE}/api/v1/integrations/health_assistant_bridge/api/${INTEGRATION}`;
const SECRET = "test-secret-16chars!";

function mockFetch(t, handler) {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url: String(url), init });
    return handler({ url: String(url), init }, calls.length);
  });
  return calls;
}

const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

test("chat decodes a rich ChatTurnResult with a task card and signs the POST", async (t) => {
  const calls = mockFetch(t, () =>
    jsonResponse({
      message: "Shall I log this?",
      session_id: "s1",
      success: true,
      message_id: "m1",
      tasks: [
        {
          proposal_id: "prop-1",
          task_type: "create_clinical_event",
          title: "Log follow-up",
          status: "proposed",
          proposed_payload: { title: "Follow-up" },
        },
      ],
      citations: [{ source: "biomarker:ldl", label: "LDL trend" }],
    }),
  );

  const client = new HealthAssistantBridgeClient(BASE, INTEGRATION, {
    apiSecret: SECRET,
  });
  const result = await client.chat("Log my lipids", "s1");

  assert.equal(result.message, "Shall I log this?");
  assert.equal(result.session_id, "s1");
  assert.equal(result.success, true);
  assert.equal(result.message_id, "m1");
  assert.equal(result.tasks[0].proposal_id, "prop-1");
  assert.equal(result.tasks[0].status, "proposed");
  assert.equal(result.citations[0].label, "LDL trend");

  assert.equal(calls.length, 1);
  const { url, init } = calls[0];
  assert.equal(url, `${API_ROOT}/ai/chat`);
  assert.equal(init.method, "POST");
  assert.deepEqual(JSON.parse(init.body.toString("utf8")), {
    message: "Log my lipids",
    session_id: "s1",
  });
  const expected = signRequest(
    SECRET,
    "POST",
    "/ai/chat",
    Buffer.from(init.body.toString("utf8"), "utf8"),
    Number(init.headers["X-Api-Timestamp"]),
  );
  assert.equal(init.headers["X-Api-Signature"], expected["X-Api-Signature"]);
});

test("chat tolerates the max-iterations shape", async (t) => {
  mockFetch(t, () =>
    jsonResponse({
      message: "I'm sorry, I reached my maximum reasoning limit.",
      session_id: null,
      success: false,
      message_id: null,
      tasks: [],
      citations: [],
    }),
  );

  const client = new HealthAssistantBridgeClient(BASE, INTEGRATION);
  const result = await client.chat("Hello");
  assert.equal(result.success, false);
  assert.equal(result.message_id, null);
  assert.deepEqual(result.tasks, []);
});

test("chat never auto-retries a failed turn", async (t) => {
  const calls = mockFetch(t, () => jsonResponse({ detail: "boom" }, 500));

  const client = new HealthAssistantBridgeClient(BASE, INTEGRATION);
  await assert.rejects(() => client.chat("Hello"));
  // A replayed chat POST would duplicate the turn — exactly one request.
  assert.equal(calls.length, 1);
});

test("getMessages decodes the envelope and threads limit into the query", async (t) => {
  const calls = mockFetch(t, () =>
    jsonResponse({
      data: [
        {
          id: "m1",
          role: "assistant",
          content: { text: "Hi" },
          tasks: [],
          citations: [],
          created_at: "2026-10-11T10:00:00Z",
        },
      ],
    }),
  );

  const client = new HealthAssistantBridgeClient(BASE, INTEGRATION, {
    apiSecret: SECRET,
  });
  const envelope = await client.getMessages("s1", 50);

  assert.equal(envelope.data.length, 1);
  assert.equal(envelope.data[0].id, "m1");
  assert.equal(envelope.data[0].role, "assistant");
  assert.equal(envelope.data[0].content?.text, "Hi");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${API_ROOT}/ai/sessions/s1/messages?limit=50`);
  // GET reads sign with an empty body when a secret is set.
  const expected = signRequest(
    SECRET,
    "GET",
    "/ai/sessions/s1/messages?limit=50",
    Buffer.alloc(0),
    Number(calls[0].init.headers["X-Api-Timestamp"]),
  );
  assert.equal(
    calls[0].init.headers["X-Api-Signature"],
    expected["X-Api-Signature"],
  );
});
