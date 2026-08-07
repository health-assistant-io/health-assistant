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
  ): Promise<Response> {
    const headers =
      rawBody && signPath ? this.signedHeaders(method, signPath, rawBody) : {};
    for (let attempt = 0; attempt < this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await fetch(url, {
          method: method,
          headers: headers,
          body: rawBody as BodyInit | undefined,
          signal: controller.signal,
        });
        if (!response.ok && RETRYABLE_STATUS.has(response.status) && attempt + 1 < this.maxRetries) {
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
        if (attempt + 1 >= this.maxRetries || !this.isTransient(err)) {
          throw err;
        }
        await this.backoff(attempt);
      } finally {
        clearTimeout(timer);
      }
    }
    throw new Error("Request to " + url + " failed after " + this.maxRetries + " attempts.");
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