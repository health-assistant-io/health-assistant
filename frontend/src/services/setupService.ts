import api from '../api/axios';
import type { SetupChecklist, ExtensionCatalog, SetupStep } from '../types/setup';

/**
 * Fetch the backend-derived setup checklist for the calling user (role
 * steps) plus, when `entity` + `entity_id` are supplied, the per-entity
 * steps. The wizard consumes `steps` + `completion` to render + track
 * progress. `completed` is authoritative; `manually_completed` distinguishes
 * a user toggle from an evaluator-detected state.
 */
export async function getSetupChecklist(opts?: {
  entity?: string;
  entity_id?: string;
}): Promise<SetupChecklist> {
  const response = await api.get<SetupChecklist>('/setup/checklist', {
    params: opts,
  });
  return response.data;
}

/**
 * Fetch the supported-extension catalog the wizard's extensions section
 * renders. Keeps the client free of hardcoded extension keys + CDC OMB
 * code lists (the backend owns both).
 */
export async function getExtensionCatalog(entity?: string): Promise<ExtensionCatalog> {
  const response = await api.get<ExtensionCatalog>('/setup/extension-catalog', {
    params: entity ? { entity } : undefined,
  });
  return response.data;
}

/**
 * Manually mark a wizard step complete (or clear the override). The override
 * persists per-user in `UserModel.settings` and folds into the step's
 * effective `completed` state on every checklist read. Pass `entity` +
 * `entity_id` for entity-scoped (patient) steps.
 */
export async function setStepManualComplete(opts: {
  stepId: string;
  completed: boolean;
  entity?: string;
  entityId?: string;
}): Promise<SetupStep> {
  const response = await api.post<SetupStep>('/setup/checklist/manual-complete', {
    step_id: opts.stepId,
    completed: opts.completed,
    entity: opts.entity,
    entity_id: opts.entityId,
  });
  return response.data;
}
