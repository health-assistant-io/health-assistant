/**
 * Per-type single-record detail registration. Imported once at app entry
 * (App.tsx) for its side effects — it registers each entity type's
 * purpose-built detail view (which reuses the entity's existing rich preview
 * component) with the detail registry.
 *
 * Sibling of `features/instances/views/index.ts` (the browse-view registry).
 * Add a type here when its single-record overlay should render the real,
 * entity-specific detail instead of the generic `InstancePreview` fallback.
 */
import { registerInstanceDetail } from '../../../components/instances/detailViewRegistry';
import { ExaminationDetail } from './ExaminationDetail';
import { ObservationDetail } from './ObservationDetail';
import { EventDetail } from './EventDetail';

// Examinations reuse ExaminationPreview (the single source of truth shared with
// the list page + ExaminationView's detail pane).
registerInstanceDetail('examination', ExaminationDetail);
// Observations reuse the canonical biomarker rendering (BiomarkerResultCard,
// same as ObservationView's grid) via useBiomarkers.
registerInstanceDetail('observation', ObservationDetail);
// Clinical events reuse ClinicalEventCard (read-only) — same as the events list.
registerInstanceDetail('event', EventDetail);

export { ExaminationDetail } from './ExaminationDetail';
export { ObservationDetail } from './ObservationDetail';
export { EventDetail } from './EventDetail';
