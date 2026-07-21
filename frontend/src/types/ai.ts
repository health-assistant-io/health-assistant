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

// ---------------------------------------------------------------------------
// `ask_user` HITL task — LLM-initiated structured questions
// ---------------------------------------------------------------------------

/** A single candidate row for ``catalog_ref`` / ``instance_ref`` questions,
 *  embedded server-side so the card renders without an extra round-trip.
 *
 *  Carries rich metadata (code, coding_system, category, is_telemetry, unit,
 *  date, status, description) so the LLM can act on the user's pick WITHOUT
 *  a follow-up ``get_*_details`` call. Mirrors the Pydantic ``CandidateRef``
 *  schema in ``backend/app/ai/tools/ask_user.py``. */
export interface QuestionCandidate {
  id: string;
  name: string;
  slug?: string | null;
  type?: string | null;
  detail?: string | null;

  // Coding / classification (catalog items)
  code?: string | null;
  coding_system?: string | null;
  category?: string | null;
  kind?: string | null;

  // Biomarker-specific
  is_telemetry?: boolean | null;
  unit?: string | null;

  // Instance-specific
  date?: string | null;
  status?: string | null;

  // Free-form description (short)
  description?: string | null;
}

export interface ChoiceOption {
  value: string;
  label: string;
  detail?: string | null;
}

/** Discriminated union of question kinds. The ``kind`` field is the
 *  discriminator. Mirrors the Pydantic schema in
 *  ``backend/app/ai/tools/ask_user.py``. */
export type AskUserQuestion =
  | {
      id: string;
      kind: 'freetext';
      prompt: string;
      help_text?: string | null;
      required?: boolean;
      placeholder?: string | null;
      multiline?: boolean;
      default?: string | null;
    }
  | {
      id: string;
      kind: 'single_choice';
      prompt: string;
      help_text?: string | null;
      required?: boolean;
      options: ChoiceOption[];
      default?: string | null;
    }
  | {
      id: string;
      kind: 'multi_choice';
      prompt: string;
      help_text?: string | null;
      required?: boolean;
      options: ChoiceOption[];
      min_select?: number;
      max_select?: number | null;
      default?: string[] | null;
    }
  | {
      id: string;
      kind: 'catalog_ref';
      prompt: string;
      help_text?: string | null;
      required?: boolean;
      catalog_type: string;
      multi?: boolean;
      prefilter?: { query?: string; is_telemetry?: boolean; kind?: string } | null;
      candidates?: QuestionCandidate[] | null;
      default?: QuestionCandidate | QuestionCandidate[] | null;
    }
  | {
      id: string;
      kind: 'instance_ref';
      prompt: string;
      help_text?: string | null;
      required?: boolean;
      entity_type: string;
      patient_scope?: boolean;
      multi?: boolean;
      candidates?: QuestionCandidate[] | null;
      default?: QuestionCandidate | QuestionCandidate[] | null;
    };

/** Shape of ``proposed_payload`` for an ``ask_user`` task. */
export interface AskUserPayload {
  summary?: string | null;
  questions: AskUserQuestion[];
}

/** Shape of ``resolved.final_payload`` for an ``ask_user`` task. Answers are
 *  keyed by question id; values depend on the question kind (string, string[],
 *  QuestionCandidate, QuestionCandidate[], or null). */
export type AskUserAnswers = Record<string, unknown>;
