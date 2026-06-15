export interface BridgeStatus {
  status: string;
  integration_id: string;
  last_synced_at: string | null;
  cursor: string | null;
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

export interface SyncPayload {
  client_version: string;
  source_system: string;
  cursor?: string | null;
  records: ClientRecord[];
}

export interface SyncResponse {
  success: boolean;
  metrics_synced?: number;
  message?: string;
  error?: string;
}

export class HealthAssistantBridgeClient {
  private baseUrl: string;
  private integrationId: string;

  constructor(baseUrl: string, integrationId: string) {
    // Ensure base URL doesn't end with a slash
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.integrationId = integrationId;
  }

  private get apiUrl(): string {
    return `${this.baseUrl}/api/v1/integrations/health_assistant_bridge/api/${this.integrationId}`;
  }

  /**
   * Check the connection status and retrieve the current sync cursor.
   */
  async getStatus(): Promise<BridgeStatus> {
    const response = await fetch(`${this.apiUrl}/status`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(`Failed to get status: ${response.statusText}`);
    }

    return response.json() as Promise<BridgeStatus>;
  }

  /**
   * Ask the Health Assistant AI to map unrecognized metrics.
   */
  async requestMapping(metrics: MetricMappingRequest[]): Promise<MapResponsePayload> {
    const response = await fetch(`${this.apiUrl}/map`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ unmapped_metrics: metrics }),
    });

    if (!response.ok) {
      throw new Error(`Failed to request mapping: ${response.statusText}`);
    }

    return response.json() as Promise<MapResponsePayload>;
  }

  /**
   * Push data into the Health Assistant platform.
   */
  async syncData(payload: SyncPayload): Promise<SyncResponse> {
    const response = await fetch(`${this.apiUrl}/sync`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Failed to sync data: ${response.statusText}`);
    }

    return response.json() as Promise<SyncResponse>;
  }
}
