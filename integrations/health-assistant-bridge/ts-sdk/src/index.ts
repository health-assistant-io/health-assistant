import * as crypto from "crypto";

export const SDK_VERSION = "1.4.0";

// Default per-request timeout (30s). The client passes an AbortSignal derived
// from this so a stalled backend can't hang a long-lived client process.
export const DEFAULT_TIMEOUT_MS = 30_000;
// Max attempts on transient network/5xx errors (simple full-jitter backoff,
// capped at 8s).
export const DEFAULT_MAX_RETRIES = 3;
const BACKOFF_CEILING_MS = 8_000;

export interface BridgeStatus {
  status: string;
  /** Server clock (epoch seconds) — present in BOTH tiers; resync a skewed
   *  client clock before signing (the replay window is ±5 min). */
  server_time?: number;
  /** Signed tier only (null/absent on the minimal unsigned probe). */
  integration_id?: string;
  last_synced_at?: string | null;
  cursor?: string | null;
  latest_sdks?: {
    python?: string;
    ts?: string;
    kotlin?: string;
    [key: string]: string | undefined;
  };
  /** The server's frontend/PWA origin (e.g. `http://host:3000`). */
  frontend_base_url?: string | null;
  /** The server's own version (display only; capability gating uses
   *  `bridge_features`). */
  server_version?: string | null;
  /** Closed feature-flag vocabulary clients gate on. Empty/absent on older
   *  servers = every gated feature falls back to its legacy path. */
  bridge_features?: string[];
}

export interface MetricMappingRequest {
  name: string;
  code?: string | null;
}

export interface MappedMetric {
  original_name: string;
  action: "map_to_existing" | "create_new";
  existing_biomarker_id?: string | null;
  new_biomarker_name?: string | null;
  new_biomarker_code?: string | null;
  new_biomarker_coding_system?: string | null;
}

export interface MapResponsePayload {
  mappings: MappedMetric[];
}

export interface ClientRecord {
  type: "quantitative" | "categorical";
  biomarker_id?: string | null;
  code?: string | null;
  coding_system?: string;
  name: string;
  value?: number | null;
  value_string?: string | null;
  unit?: string | null;
  timestamp?: string | null;
  reference_range?: {
    low?: number;
    high?: number;
  } | null;
  interpretation?: string | null;
  performer?: string | null;
}

export interface ClientExaminationRecord {
  id?: string | null;
  date?: string | null;
  lab_name?: string | null;
  notes?: string | null;
  patient_notes?: string | null;
  category?: string | null;
  diagnoses?: string[];
  impressions?: string | null;
  records?: ClientRecord[] | null;
}

export interface SyncPayload {
  client_version: string;
  source_system: string;
  cursor?: string | null;
  records?: ClientRecord[] | null;
  examinations?: ClientExaminationRecord[] | null;
}

export interface SyncResponse {
  success: boolean;
  metrics_synced?: number;
  message?: string;
  error?: string;
}

// --- AI assistant (bridge /ai/* paths) -------------------------------------

/** One message row from `GET /ai/sessions/{id}/messages` (newest first).
 * `content` is `{text: …}`; `tasks` carries HITL proposal cards. */
export interface BridgeChatMessage {
  id: string;
  role?: string;
  content?: { text?: string; [key: string]: unknown };
  tasks?: Record<string, unknown>[];
  citations?: Record<string, unknown>[];
  created_at?: string | null;
}

/** Reply of `POST /ai/chat` — one non-streaming turn. `tasks` carries the
 * HITL proposal cards persisted with the assistant row (render them without
 * a messages refetch); `message_id` is null on the max-iterations path (no
 * assistant row), where `tasks`/`citations` are empty. */
export interface ChatTurnResult {
  message: string | null;
  session_id: string | null;
  success: boolean;
  message_id?: string | null;
  tasks?: Record<string, unknown>[];
  citations?: Record<string, unknown>[];
}

/** Reply of `PATCH /ai/sessions/{id}` — the renamed session row. */
export interface SessionRenameResult {
  id: string;
  title?: string | null;
  updated_at?: string | null;
}

/** Reply of `POST /ai/transcribe` — voice input. `success` is false when
 * the provider transcribed nothing (silence); the audio is ephemeral
 * server-side (never persisted — the gateway rate budget + audit trail are
 * the only ledger). */
export interface TranscriptionResult {
  text: string | null;
  success: boolean;
}

export interface BridgeClientOptions {
  /** HMAC secret. When set, requests are signed (X-Api-Signature) —
   * including the full `/status` discovery tier. */
  apiSecret?: string;
  /** Per-request timeout in ms (default 30000). */
  timeoutMs?: number;
  /** Max attempts on transient network/5xx errors (default 3). */
  maxRetries?: number;
}

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

/**
 * Thrown by `request`/`requestLines` on a non-2xx response — for streams
 * always BEFORE the first emission. `code` is the stable error code decoded
 * from the typed detail envelope `{detail: {code, message}}`; undefined for
 * older servers that answer with a plain-string detail (the raw body stays
 * available as `body`). The closed vocabulary (`not_found`/404,
 * `turn_conflict`/409, `hitl_conflict`/409, `validation`/400) is documented
 * in the bridge docs' Errors table — switch on `err.code`.
 */
export class BridgeException extends Error {
  /** The HTTP status code. */
  readonly statusCode: number;
  /** The raw response body text. */
  readonly body: string;
  /** Stable error code from the typed detail envelope (undefined when the
   *  detail is a plain string). */
  readonly code?: string;

  constructor(statusCode: number, body: string, code?: string) {
    super(`HTTP ${statusCode}: ${body}`);
    this.name = "BridgeException";
    this.statusCode = statusCode;
    this.body = body;
    this.code = code;
  }

  /** Decode a raw error body into a BridgeException — the typed
   *  `{detail: {code, message}}` envelope yields `code`; a plain-string (or
   *  non-JSON) detail leaves it undefined. */
  static from(statusCode: number, body: string): BridgeException {
    let code: string | undefined;
    try {
      const detail = (JSON.parse(body) as { detail?: unknown }).detail;
      if (
        detail !== null &&
        typeof detail === "object" &&
        typeof (detail as { code?: unknown }).code === "string"
      ) {
        code = (detail as { code: string }).code;
      }
    } catch {
      code = undefined;
    }
    return new BridgeException(statusCode, body, code);
  }
}

/** Strip one trailing CR so `\r\n`-framed lines match the Kotlin
 *  `readUTF8Line` contract. */
function stripTrailingCarriageReturn(line: string): string {
  return line.endsWith("\r") ? line.slice(0, -1) : line;
}

/**
 * Build the HMAC signature headers for a signed request, mirroring the
 * server's canonical form:
 *   METHOD\n<path>\n<timestamp>\n<raw_body>
 *
 * @param secret The plaintext API secret configured on the bridge instance.
 * @param method HTTP method (POST/GET/...).
 * @param path The API path component after the integration id, with a
 *   leading "/" (e.g. "/sync").
 * @param rawBody The exact request body bytes the signature covers.
 * @param timestamp Override the epoch-second timestamp (mainly for tests).
 * @returns The X-Api-Signature and X-Api-Timestamp headers.
 */
export function signRequest(
  secret: string,
  method: string,
  path: string,
  rawBody: Buffer,
  timestamp: number = Math.floor(Date.now() / 1000),
): Record<string, string> {
  if (!secret) {
    throw new Error("apiSecret is required to sign a request.");
  }
  const ts = String(timestamp);
  const canonical =
    method.toUpperCase() + "\n" + path + "\n" + ts + "\n" + rawBody.toString("utf8");
  const digest = crypto.createHmac("sha256", secret).update(canonical).digest("hex");
  return { "X-Api-Signature": digest, "X-Api-Timestamp": ts };
}

/**
 * Client for the Health Assistant Bridge two-way API proxy (Node/TypeScript
 * twin of the Kotlin `BridgeClient`). Every request is HMAC-signed when
 * `apiSecret` is set.
 *
 * ## Retry matrix
 *
 * Two request classes, selected by the private `request()`'s `retry` option:
 *
 * - **Retried** (default): reads (`getStatus`, every typed getter),
 *   `/sync`, and idempotent mutations (Phase-6 writes keyed on
 *   `client_request_id` / `id`, notification read/dismiss, session rename),
 *   plus the read-only voice-input {@link transcribe} POST (audio is
 *   ephemeral server-side — a replay just re-transcribes).
 *   429/5xx answers and transient fetch failures are retried with
 *   full-jitter backoff (up to `maxRetries` attempts). Retrying is safe
 *   because a replay either converges (idempotent writes) or was never
 *   applied.
 * - **Never retried**: the AI turn POSTs — {@link chat}, and every stream
 *   that goes through {@link requestLines} (`POST /ai/chat/stream` via
 *   {@link chatStream} and `POST /ai/sessions/{id}/resume` via
 *   {@link resumeChatStream}). A turn POST is not idempotent: replaying it
 *   after a transport failure duplicates the user message and the LLM spend
 *   (and a partially-delivered answer cannot be resumed mid-stream). Those
 *   paths are single-attempt by construction: a 429/5xx response and any
 *   network failure — including one before the request was dispatched —
 *   surface to the caller without a second attempt. **Recovery is starting
 *   a NEW turn** (re-send the message, or {@link resumeChatStream} for a
 *   HITL continuation); the server-side single-flight turn lock keeps that
 *   new turn from colliding with one that is still running.
 */
export class HealthAssistantBridgeClient {
  private baseUrl: string;
  private integrationId: string;
  private apiSecret?: string;
  private timeoutMs: number;
  private maxRetries: number;

  constructor(baseUrl: string, integrationId: string, options: BridgeClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.integrationId = integrationId;
    this.apiSecret = options.apiSecret;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
  }

  private get apiUrl(): string {
    return (
      this.baseUrl +
      "/api/v1/integrations/health_assistant_bridge/api/" +
      this.integrationId
    );
  }

  /**
   * Two-tier status probe. Without an `apiSecret` it is the unsigned
   * pre-pairing probe — the server answers the minimal
   * `{status, server_time}` shape. With `apiSecret` set the request is
   * HMAC-signed and returns the full discovery payload: cursor, latest
   * SDK versions, frontend origin, server version, and the
   * `bridge_features` flags.
   */
  async getStatus(): Promise<BridgeStatus> {
    const response = await this.request(
      "GET",
      this.apiUrl + "/status",
      Buffer.alloc(0),
      "/status",
    );
    const data: BridgeStatus = (await response.json()) as BridgeStatus;
    if (data.latest_sdks?.ts && data.latest_sdks.ts !== SDK_VERSION) {
      console.warn(
        "[Health Assistant Bridge] Notice: You are using SDK version " +
          SDK_VERSION +
          ", but the latest available is " +
          data.latest_sdks.ts +
          ". Please consider updating.",
      );
    }
    return data;
  }

  /** Ask the Health Assistant AI to map unrecognized metrics. */
  async requestMapping(metrics: MetricMappingRequest[]): Promise<MapResponsePayload> {
    const rawBody = Buffer.from(
      JSON.stringify({ unmapped_metrics: metrics }),
      "utf8",
    );
    const response = await this.request(
      "POST",
      this.apiUrl + "/map",
      rawBody,
      "/map",
    );
    return (await response.json()) as MapResponsePayload;
  }

  /** Push data into the Health Assistant platform. */
  async syncData(payload: SyncPayload): Promise<SyncResponse> {
    const rawBody = Buffer.from(JSON.stringify(payload), "utf8");
    const response = await this.request(
      "POST",
      this.apiUrl + "/sync",
      rawBody,
      "/sync",
    );
    return (await response.json()) as SyncResponse;
  }

  /**
   * Line-by-line reading of a signed streaming response body (SSE): a
   * fetch-based reader (`response.body.getReader()` + `TextDecoder`) that
   * yields each raw line WITHOUT its newline — including empty separator
   * lines and `:` comment frames, matching the Kotlin SDK's `requestLines`
   * contract — so the caller parses the SSE framing itself. Requires a
   * `fetch` with streamable response bodies (Node >= 18; the SDK's
   * `engines` floor already guarantees it).
   *
   * Deliberately NO retry and NO whole-request timeout: the paths streamed
   * through this reader are AI turn POSTs, and a replay after a transport
   * failure would duplicate the turn — the user message and the LLM call —
   * so there is exactly one fetch: a 429/5xx answer throws
   * {@link BridgeException} before the first emission, and a mid-body
   * stream error surfaces to the consumer with no second request. Recovery
   * is the caller starting a NEW turn (see the class retry matrix).
   */
  async *requestLines(
    method: string,
    path: string,
    body?: Buffer,
  ): AsyncGenerator<string> {
    const rawBody = body ?? Buffer.alloc(0);
    const headers = this.signedHeaders(method, path, rawBody);
    const response = await fetch(this.apiUrl + path, {
      method: method,
      headers: headers,
      body: rawBody.length > 0 ? (rawBody as BodyInit) : undefined,
    });
    if (!response.ok) {
      throw BridgeException.from(response.status, await response.text());
    }
    if (!response.body) {
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        pending += decoder.decode(value, { stream: true });
        let newline = pending.indexOf("\n");
        while (newline >= 0) {
          yield stripTrailingCarriageReturn(pending.slice(0, newline));
          pending = pending.slice(newline + 1);
          newline = pending.indexOf("\n");
        }
      }
      pending += decoder.decode();
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
    if (pending.length > 0) {
      yield stripTrailingCarriageReturn(pending);
    }
  }

  /**
   * `POST /ai/chat/stream` — one streaming chat turn. Builds and signs the
   * `{message, session_id?}` body and yields the raw SSE lines via
   * {@link requestLines} (`data:` frames, empty separators, `:` comments).
   * Like every AI turn POST this is **never retried** (see the class retry
   * matrix): a failed turn is recovered by starting a NEW turn.
   */
  async *chatStream(message: string, sessionId?: string): AsyncGenerator<string> {
    const payload: Record<string, unknown> =
      sessionId === undefined ? { message } : { message, session_id: sessionId };
    yield* this.requestLines(
      "POST",
      "/ai/chat/stream",
      Buffer.from(JSON.stringify(payload), "utf8"),
    );
  }

  /**
   * `POST /ai/sessions/{id}/resume` — the HITL continuation turn after the
   * user resolved task cards. `messageId` selects which assistant message's
   * resolved tasks are summarized; `undefined` omits the field so the
   * server picks the most recent task-bearing message. Yields the raw SSE
   * lines via {@link requestLines}. Like every AI turn POST this is
   * **never retried** (see the class retry matrix): a failed resume is
   * recovered by starting a new turn.
   */
  async *resumeChatStream(
    sessionId: string,
    messageId?: string,
  ): AsyncGenerator<string> {
    const payload: Record<string, unknown> =
      messageId === undefined ? {} : { message_id: messageId };
    yield* this.requestLines(
      "POST",
      `/ai/sessions/${sessionId}/resume`,
      Buffer.from(JSON.stringify(payload), "utf8"),
    );
  }

  // --- AI assistant ---------------------------------------------------------

  /** One non-streaming chat turn over `POST /ai/chat`. The reply carries the
   * persisted assistant row's id + HITL `tasks` cards + `citations`, so a
   * turn with proposals renders without a messages refetch. NEVER
   * auto-retries (`retry: false` — one dispatch): a replayed chat POST would
   * duplicate the user message and the LLM call. Recovery for a failed turn
   * is starting a NEW turn (see the class retry matrix). */
  async chat(message: string, sessionId?: string): Promise<ChatTurnResult> {
    const payload: Record<string, string> = { message };
    if (sessionId) {
      payload.session_id = sessionId;
    }
    const path = "/ai/chat";
    const rawBody = Buffer.from(JSON.stringify(payload), "utf8");
    const response = await this.request("POST", this.apiUrl + path, rawBody, path, {
      retry: false,
    });
    return (await response.json()) as ChatTurnResult;
  }

  /** The session's messages (`GET /ai/sessions/{id}/messages`), newest
   * first. `limit` defaults to 200 server-side (max 500). */
  async getMessages(
    sessionId: string,
    limit?: number,
  ): Promise<{ data: BridgeChatMessage[] }> {
    const path =
      "/ai/sessions/" + sessionId + "/messages" + (limit != null ? "?limit=" + limit : "");
    const response = await this.request("GET", this.apiUrl + path, Buffer.alloc(0), path);
    return (await response.json()) as { data: BridgeChatMessage[] };
  }

  /** Rename a session (`PATCH /ai/sessions/{id}`) — title non-blank, ≤ 200
   * chars (validated server-side). Idempotent, so the default retry policy
   * applies. Returns the renamed row. */
  async renameSession(sessionId: string, title: string): Promise<SessionRenameResult> {
    const path = "/ai/sessions/" + sessionId;
    const rawBody = Buffer.from(JSON.stringify({ title }), "utf8");
    const response = await this.request("PATCH", this.apiUrl + path, rawBody, path);
    return (await response.json()) as SessionRenameResult;
  }

  /** Voice input over `POST /ai/transcribe`: base64 `audio` + `mime` (a
   * full mime with parameters, e.g. `audio/webm;codecs=opus`, is
   * normalized and allowlisted server-side; oversize audio or a non-audio
   * mime is a typed 400 `validation`). The audio is **ephemeral**
   * server-side — transcribed and discarded, never persisted — so this is
   * read-only (a replay just re-transcribes) and the default retry policy
   * applies, unlike the AI turn POSTs (see the class retry matrix).
   * Counts toward the `/ai/*` rate budget. */
  async transcribe(audio: Buffer, mime: string): Promise<TranscriptionResult> {
    const path = "/ai/transcribe";
    const rawBody = Buffer.from(
      JSON.stringify({ data: audio.toString("base64"), mime }),
      "utf8",
    );
    const response = await this.request("POST", this.apiUrl + path, rawBody, path);
    return (await response.json()) as TranscriptionResult;
  }

  private signedHeaders(method: string, path: string, rawBody: Buffer): Record<string, string> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiSecret) {
      Object.assign(headers, signRequest(this.apiSecret, method, path, rawBody));
    }
    return headers;
  }

  private async request(
    method: string,
    url: string,
    rawBody?: Buffer,
    signPath?: string,
    opts?: { retry?: boolean },
  ): Promise<Response> {
    const headers =
      rawBody && signPath ? this.signedHeaders(method, signPath, rawBody) : {};
    const attempts = opts?.retry === false ? 1 : this.maxRetries;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await fetch(url, {
          method: method,
          headers: headers,
          body:
            rawBody && rawBody.length > 0
              ? (rawBody as BodyInit)
              : undefined,
          signal: controller.signal,
        });
        if (!response.ok && RETRYABLE_STATUS.has(response.status) && attempt + 1 < attempts) {
          await this.backoff(attempt);
          continue;
        }
        if (!response.ok) {
          throw BridgeException.from(
            response.status,
            await response.text(),
          );
        }
        return response;
      } catch (err) {
        if (attempt + 1 >= attempts || !this.isTransient(err)) {
          throw err;
        }
        await this.backoff(attempt);
      } finally {
        clearTimeout(timer);
      }
    }
    throw new Error("Request to " + url + " failed after " + attempts + " attempts.");
  }

  private isTransient(err: unknown): boolean {
    if (err instanceof Error) {
      const name = err.name;
      return name === "AbortError" || name === "TypeError" || name === "FetchError";
    }
    return false;
  }

  private backoff(attempt: number): Promise<void> {
    const wait = Math.min(BACKOFF_CEILING_MS, Math.pow(2, attempt) * 1000);
    const jitter = Math.random() * wait;
    return new Promise((resolve) => setTimeout(resolve, jitter));
  }
}