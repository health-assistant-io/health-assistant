/**
 * Per-type browse-view registration. Imported once at app entry (App.tsx) for
 * its side effects — it registers each entity type's purpose-built browse view
 * (which reuses existing robust components) with the view registry.
 *
 * This module (not the adapter modules) owns the heavy UI imports, keeping the
 * data adapters free of UI dependencies and free of import cycles.
 */
import { registerInstanceView } from '../../../components/instances/viewRegistry';
import { ExaminationView } from './ExaminationView';
import { ObservationView } from './ObservationView';

// Examinations reuse the robust ExaminationCard list + ExaminationPreview.
registerInstanceView('examination', ExaminationView);
// Observations (biomarker results) render as a biomarker card grid (trends-style).
registerInstanceView('observation', ObservationView);

export { ExaminationView } from './ExaminationView';
export { ObservationView } from './ObservationView';
