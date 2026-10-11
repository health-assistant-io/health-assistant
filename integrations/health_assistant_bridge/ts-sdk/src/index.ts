import * as crypto from "crypto";

export const SDK_VERSION = "1.3.0";

// Default per-request timeout (30s). The client passes an AbortSignal derived
// from this so a stalled backend can't hang a long-lived client process.
export const DEFAULT_TIMEOUT_MS = 30_000;
// Max attempts on transient network/5xx errors (simple full-jitter backoff,
// capped at 8s).
export const DEFAULT_MAX_RETRIES = 3;
const BACKOFF_CEILING_MS = 8_000;

export interface BridgeStatus {
  status: string;
  integration_id: string;
  last_synced_at: string | null;
  cursor: string | null;
  latest_sdks?: {
    python?: string;
    ts?: string;
    [key: string]: string | undefined;
  };
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

export interface BridgeClientOptions {
  /** HMAC secret. When set, /map and /sync are signed (X-Api-Signature). */
  apiSecret?: string;
  /** Per-request timeout in ms (default 30000). */
  timeoutMs?: number;
  /** Max attempts on transient network/5xx errors (default 3). */
  maxRetries?: number;
}

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

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
   * Check the connection status and retrieve the current sync cursor.
   * Never HMAC-signed (connectivity + SDK-discovery probe).
   */
  async getStatus(): Promise<BridgeStatus> {
    const response = await this.request("GET", this.apiUrl + "/status");
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

  // --- AI assistant ---------------------------------------------------------

  /** One non-streaming chat turn over `POST /ai/chat`. The reply carries the
   * persisted assistant row's id + HITL `tasks` cards + `citations`, so a
   * turn with proposals renders without a messages refetch. NEVER
   * auto-retries: a replayed chat POST would duplicate the user message and
   * the LLM call. */
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
          body: rawBody as BodyInit | undefined,
          signal: controller.signal,
        });
        if (!response.ok && RETRYABLE_STATUS.has(response.status) && attempt + 1 < attempts) {
          await this.backoff(attempt);
          continue;
        }
        if (!response.ok) {
          throw new Error(
            "Request to " + url + " failed: " + response.status + " " + response.statusText,
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