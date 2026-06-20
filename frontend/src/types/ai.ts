export interface ToolCallInfo {
  name: string;
  args?: string;
  result?: string;
  status: 'starting' | 'executing' | 'finished';
}

/**
 * A human-in-the-loop task card proposed by the assistant.
 * Mirrors the backend `[HITL_TASK]` payload contract.
 */
export interface TaskInfo {
  schema_version: number;
  proposal_id: string;
  task_type: string;
  title?: string;
  /** proposed | confirmed | failed | dismissed */
  status: 'proposed' | 'confirmed' | 'failed' | 'dismissed';
  proposed_payload: Record<string, any>;
  context?: Record<string, any>;
  created_at?: string;
  resolved?: {
    confirmed_by?: string;
    final_payload?: Record<string, any>;
    result?: Record<string, any>;
    error?: string;
    at?: string;
  } | null;
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: ToolCallInfo[];
  citations?: string[];
  tasks?: TaskInfo[];
  isExecuting?: boolean;
  /** True for messages loaded from server history (not streamed in this
   * session). Used to suppress auto-resume on already-resolved HITL cards
   * that the user is just reviewing. */
  _loadedFromHistory?: boolean;
}
