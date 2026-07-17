/**
 * Unified Instance Picker barrel.
 *
 * Generic, domain-agnostic primitives for browsing/selecting patient-scoped
 * clinical records. Per-entity behavior is supplied by adapters registered
 * from `features/instances/adapters` (Phase 3); the components here never
 * touch an entity's raw shape.
 *
 * See `dev/plans/instance-browser-unified-picker-2026-07-16.md`.
 */
export * from './types';
export {
  registerAdapter,
  getAdapter,
  getAdapters,
  hasAdapter,
} from './instanceRegistry';
export { InstanceBrowser } from './InstanceBrowser';
export type { InstanceBrowserProps } from './InstanceBrowser';
export { InstancePicker, selectionKey } from './InstancePicker';
export type { InstancePickerProps } from './InstancePicker';
export { InstanceBrowseModal } from './InstanceBrowseModal';
export type { InstanceBrowseModalProps } from './InstanceBrowseModal';
export { InstancePreview } from './InstancePreview';
export type { InstancePreviewProps } from './InstancePreview';
export { InstanceField } from './InstanceField';
export type { InstanceFieldProps } from './InstanceField';
