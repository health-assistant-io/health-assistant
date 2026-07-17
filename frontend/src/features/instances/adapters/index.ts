/**
 * Instance adapters barrel.
 *
 * Importing this module registers all seven entity adapters with the instance
 * registry (side effect), so any component that imports the picker primitives
 * and this barrel gets the full set wired. Per-entity behavior lives in the
 * adapter modules; the generic components stay domain-agnostic.
 *
 * See `dev/plans/instance-browser-unified-picker-2026-07-16.md` (Phase 3).
 */
import { registerAdapter } from '../../../components/instances/instanceRegistry';
import { examinationAdapter } from './examinationAdapter';
import { medicationAdapter } from './medicationAdapter';
import { observationAdapter } from './observationAdapter';
import { documentAdapter } from './documentAdapter';
import { eventAdapter } from './eventAdapter';
import { allergyAdapter } from './allergyAdapter';
import { vaccineAdapter } from './vaccineAdapter';

registerAdapter(examinationAdapter);
registerAdapter(medicationAdapter);
registerAdapter(observationAdapter);
registerAdapter(documentAdapter);
registerAdapter(eventAdapter);
registerAdapter(allergyAdapter);
registerAdapter(vaccineAdapter);

export { examinationAdapter } from './examinationAdapter';
export { medicationAdapter } from './medicationAdapter';
export { observationAdapter } from './observationAdapter';
export { documentAdapter } from './documentAdapter';
export { eventAdapter } from './eventAdapter';
export { allergyAdapter } from './allergyAdapter';
export { vaccineAdapter } from './vaccineAdapter';
