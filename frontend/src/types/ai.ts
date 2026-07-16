export interface ToolCallInfo {
  name: string;
  args?: string;
  result?: string;
  status: 'starting' | 'executing' | 'finished';
}

/**
 * A single image attached to a chat message, represented as an RFC 2397
 * data URL (``data:image/...;base64,...``). This is the exact format the
 * backend ships to vision models AND persists inside ``ChatMessage.content``,
 * so the same value flows: picker -> service -> DB -> history reload.
 */
export type ChatImageAttachment = string;

/** Lifecycle of a pending attachment in the composer (before it is sent). */
export enum ChatAttachmentStatus {
  /** Reading/encoding the file into a data URL. */
  Encoding = 'encoding',
  /** Ready to send. */
  Ready = 'ready',
  /** Rejected by client-side validation (wrong type / too large). */
  Rejected = 'rejected',
}

/** A composer-side pending attachment with metadata for the preview UI. */
export interface PendingChatAttachment {
  /** Unique ephemeral id for React keys + removal. */
  id: string;
  /** RFC 2397 data URL once ``status === Ready``. */
  dataUrl?: string;
  /** Original file name, for the preview chip. */
  name: string;
  /** Decoded byte size, for the size badge. */
  size: number;
  /** Lowercased MIME type. */
  mimeType: string;
  status: ChatAttachmentStatus;
  /** Human-readable reason when ``status === Rejected``. */
  error?: string;
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
  /** Image attachments (data URLs) for multimodal user messages. */
  images?: ChatImageAttachment[];
  toolCalls?: ToolCallInfo[];
  citations?: string[];
  tasks?: TaskInfo[];
  isExecuting?: boolean;
  /** True for messages loaded from server history (not streamed in this
   * session). Used to suppress auto-resume on already-resolved HITL cards
   * that the user is just reviewing. */
  _loadedFromHistory?: boolean;
}
