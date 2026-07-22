import React from 'react';
import { TaskInfo } from '../../../types/ai';
import {
  CreateClinicalEventHandler,
  renderClinicalEventSummary,
} from './handlers/CreateClinicalEventHandler';
import {
  AddBiomarkerHandler,
  renderAddBiomarkerSummary,
} from './handlers/AddBiomarkerHandler';
import {
  CreateBiomarkerDefinitionHandler,
  renderCreateBiomarkerSummary,
  renderCreateBiomarkerOutcome,
} from './handlers/CreateBiomarkerDefinitionHandler';
import {
  CreateMedicationDefinitionHandler,
  renderCreateMedicationSummary,
  renderCreateMedicationOutcome,
} from './handlers/CreateMedicationDefinitionHandler';
import {
  CreateAllergyHandler,
  renderAllergySummary,
  renderAllergyOutcome,
} from './handlers/CreateAllergyHandler';
import {
  CreateAllergyDefinitionHandler,
  renderCreateAllergySummary,
  renderCreateAllergyOutcome,
} from './handlers/CreateAllergyDefinitionHandler';
import { AskUserHandler, renderAskUserSummary, renderAskUserOutcome } from './handlers/AskUserHandler';
import {
  GenerateAnatomyGraphHandler,
  renderAnatomyGraphSummary,
  renderAnatomyGraphOutcome,
} from './handlers/GenerateAnatomyGraphHandler';
import {
  CreateMedicationHandler,
  renderMedicationSummary,
  renderMedicationOutcome,
} from './handlers/CreateMedicationHandler';
import { Sparkles, Activity, CheckCircle2, XCircle, AlertCircle, Pill, Beaker, ShieldAlert, HelpCircle, Network } from 'lucide-react';

export type HitlTaskStatus = TaskInfo['status'];

/**
 * Statuses that mark a HITL task as "finished acting on" — the user has
 * either confirmed, dismissed, or the commit failed. Once every task on a
 * message reaches one of these states, the auto-resume continuation turn
 * becomes eligible to fire.
 *
 * Mirrors `HitlTaskStatus.terminal()` on the backend (app/models/enums.py).
 */
export const TERMINAL_HITL_STATUSES: ReadonlySet<HitlTaskStatus> = new Set([
  'confirmed',
  'dismissed',
  'failed',
]);

export interface HitlHandlerProps {
  task: TaskInfo;
  sessionId: string | null;
  /** Notify the parent that the task's status changed (e.g. confirmed/rejected)
   * so the message state and any optimistic UI stay in sync. Also closes any
   * open modal hosting this handler. */
  onResolved: (updated: TaskInfo) => void;
  /** Close the hosting modal WITHOUT resolving (no status change). The proposal
   * stays pending and can be reopened. Maps to a "Cancel" affordance. */
  onCancel: () => void;
}

export interface HitlTaskHandler {
  taskType: string;
  /** Lucide icon for the card header. */
  icon: React.ComponentType<{ className?: string }>;
  /** Accent color token (e.g. 'blue', 'indigo'). */
  accent: 'blue' | 'indigo' | 'emerald' | 'amber' | 'rose';
  /** Compact, read-only preview rendered inside the chat card body. Should
   * surface 2-3 key fields so the user can decide whether to open the modal. */
  renderSummary: (task: TaskInfo) => React.ReactNode;
  /** Optional: renders result/change details inside the RESOLVED (collapsed)
   *  card — e.g. counts of nodes/edges imported, links created/failed. Return
   *  null when there is nothing meaningful to show. Only called for terminal
   *  tasks (confirmed/failed/dismissed). */
  renderOutcome?: (task: TaskInfo) => React.ReactNode | null;
  /** The full interactive form, rendered INSIDE the modal popup. Owns its own
   * actions (confirm/dismiss) via onResolved. */
  FormComponent: React.FC<HitlHandlerProps>;
  /** When true, the form renders INLINE in the card body (no modal, no
   *  "Review & Edit" button). Use for read-only / no-write proposals where
   *  the user fills the form directly in the chat scrollback — e.g.
   *  ``ask_user`` questions. The form owns its full footer (Cancel/Submit). */
  inline?: boolean;
}

/** Registry of all known HITL task handlers. To add a new task type, register
 *  it here with its summary renderer + form component. */
const REGISTRY: Record<string, HitlTaskHandler> = {
  create_clinical_event: {
    taskType: 'create_clinical_event',
    icon: Sparkles,
    accent: 'indigo',
    renderSummary: renderClinicalEventSummary,
    FormComponent: CreateClinicalEventHandler,
  },
  add_biomarker_to_examination: {
    taskType: 'add_biomarker_to_examination',
    icon: Activity,
    accent: 'emerald',
    renderSummary: renderAddBiomarkerSummary,
    FormComponent: AddBiomarkerHandler,
  },
  add_medication: {
    taskType: 'add_medication',
    icon: Pill,
    accent: 'amber',
    renderSummary: renderMedicationSummary,
    renderOutcome: renderMedicationOutcome,
    FormComponent: CreateMedicationHandler,
  },
  create_biomarker_definition: {
    taskType: 'create_biomarker_definition',
    icon: Beaker,
    accent: 'blue',
    renderSummary: renderCreateBiomarkerSummary,
    renderOutcome: renderCreateBiomarkerOutcome,
    FormComponent: CreateBiomarkerDefinitionHandler,
  },
  create_medication_definition: {
    taskType: 'create_medication_definition',
    icon: Pill,
    accent: 'indigo',
    renderSummary: renderCreateMedicationSummary,
    renderOutcome: renderCreateMedicationOutcome,
    FormComponent: CreateMedicationDefinitionHandler,
  },
  add_allergy: {
    taskType: 'add_allergy',
    icon: ShieldAlert,
    accent: 'rose',
    renderSummary: renderAllergySummary,
    renderOutcome: renderAllergyOutcome,
    FormComponent: CreateAllergyHandler,
  },
  create_allergy_definition: {
    taskType: 'create_allergy_definition',
    icon: ShieldAlert,
    accent: 'amber',
    renderSummary: renderCreateAllergySummary,
    renderOutcome: renderCreateAllergyOutcome,
    FormComponent: CreateAllergyDefinitionHandler,
  },
  ask_user: {
    taskType: 'ask_user',
    icon: HelpCircle,
    accent: 'indigo',
    renderSummary: renderAskUserSummary,
    renderOutcome: renderAskUserOutcome,
    FormComponent: AskUserHandler,
    inline: true,
  },
  generate_anatomy_graph: {
    taskType: 'generate_anatomy_graph',
    icon: Network,
    accent: 'indigo',
    renderSummary: renderAnatomyGraphSummary,
    renderOutcome: renderAnatomyGraphOutcome,
    FormComponent: GenerateAnatomyGraphHandler,
  },
};

export function getHitlHandler(taskType: string): HitlTaskHandler | undefined {
  return REGISTRY[taskType];
}

export const HITL_STATUS_META: Record<
  HitlTaskStatus,
  { icon: React.ComponentType<{ className?: string }>; tone: string; labelKey: string }
> = {
  proposed: { icon: AlertCircle, tone: 'amber', labelKey: 'ai_chat.hitl.status.proposed' },
  confirmed: { icon: CheckCircle2, tone: 'emerald', labelKey: 'ai_chat.hitl.status.confirmed' },
  failed: { icon: XCircle, tone: 'rose', labelKey: 'ai_chat.hitl.status.failed' },
  dismissed: { icon: XCircle, tone: 'gray', labelKey: 'ai_chat.hitl.status.dismissed' },
};
