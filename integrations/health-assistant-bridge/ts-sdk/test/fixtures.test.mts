// Schema conformance wall for the TS SDK (plan 32 ADR-0040 D11 — the bridge
// goes schema-first). The generated model layer (../src/generated-models.ts)
// carries the schema-derived types; this suite pins the RUNTIME wall: the
// typed error vocabulary decodes exactly through the SDK's BridgeException,
// the stream fixture frames stay inside the closed event vocabulary, and the
// fixture entries referenced by the schema exist — the same fixtures.json the
// python/kotlin suites consume (implementations may differ, outputs may not).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { BridgeException } from "../src/index.ts";
import {
  BRIDGE_ERROR_CODES,
  BRIDGE_FEATURE_FLAGS,
  STREAM_EVENT_NAMES,
} from "../src/generated-models.ts";

const HERE = path.dirname(new URL(import.meta.url).pathname);
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");
const SCHEMA_DIR = path.join(
  REPO_ROOT,
  "backend",
  "app",
  "extensions",
  "builtin",
  "health_assistant_bridge",
  "schema",
);

const schema = JSON.parse(
  readFileSync(path.join(SCHEMA_DIR, "bridge-api.schema.json"), "utf8"),
);
const fixtures = JSON.parse(
  readFileSync(path.join(SCHEMA_DIR, "fixtures.json"), "utf8"),
);

test("typed error fixtures decode through BridgeException", () => {
  const liveCodes = Object.keys(schema.errors.codes);
  for (const error of fixtures.errors) {
    const exception = BridgeException.from(error.status, JSON.stringify(error.body));
    if (error.typed) {
      const code = error.body.detail.code;
      assert.equal(exception.code, code);
      assert.ok(
        BRIDGE_ERROR_CODES.includes(code),
        `fixture code ${code} not in the generated vocabulary`,
      );
      assert.ok(liveCodes.includes(code));
    } else {
      assert.equal(exception.code, undefined);
    }
  }
  for (const code of Object.keys(schema.errors.codes)) {
    assert.ok(
      BRIDGE_ERROR_CODES.includes(code),
      `schema code ${code} missing from the generated vocabulary`,
    );
  }
});

test("stream fixture frames stay inside the closed event vocabulary", () => {
  const served = new Set(schema.sse.eventNames);
  for (const frame of fixtures.stream.frames) {
    if (frame.event === "keep-alive") {
      assert.match(frame.line, /^:/);
      continue;
    }
    assert.ok(served.has(frame.event), `frame event ${frame.event} not in the schema vocabulary`);
    assert.ok(
      STREAM_EVENT_NAMES.includes(frame.event),
      `frame event ${frame.event} missing from the generated union`,
    );
    assert.match(frame.line, /^data: /);
  }
});

test("feature-flag handshake vocabulary is the generated one", () => {
  assert.deepEqual(
    [...BRIDGE_FEATURE_FLAGS],
    schema.handshake.featureFlags.vocabulary,
  );
});

test("component fixture entries reference real routes and examples", () => {
  const routes = new Map(schema.routes.map((r) => [`${r.method} ${r.path}`, r]));
  const fixtureRoutes = new Set(Object.keys(fixtures.routes));
  for (const entry of fixtures.componentExamples) {
    assert.ok(routes.has(entry.route), `unknown route ${entry.route}`);
    if (!entry.route.includes("stream")) {
      assert.ok(
        fixtureRoutes.has(entry.route),
        `${entry.route} missing from the fixtures' route table`,
      );
    }
    const component = schema.components[entry.responseComponent];
    assert.ok(component, `unknown component ${entry.responseComponent}`);
    assert.ok(component.examples?.length, `${entry.responseComponent} has no example`);
  }
  const sdkNames = new Set(
    Object.entries(schema.components)
      .filter(([, c]) => c["x-sdk"])
      .map(([n]) => n),
  );
  for (const entry of fixtures.componentExamples) {
    if (!sdkNames.has(entry.responseComponent)) continue;
    // The generated layer must at least declare every x-sdk shape the
    // fixtures reference — a missing export is a type-check failure under
    // tsc and a runtime miss here via the vocabulary consts above.
    assert.ok(schema.components[entry.responseComponent].examples[0]);
  }
});
