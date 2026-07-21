/**
 * Shared vaccination-status metadata — the single source of truth for the
 * status badge colour + label so the card, form, and list filter never drift.
 *
 * `ImmunizationStatus` mirrors the FHIR R4 `Immunization.status` value set
 * (backend `app.models.enums.ImmunizationStatus`).
 */
import type { ImmunizationStatus } from '../../types/vaccine';

export interface ImmunizationStatusMeta {
  value: ImmunizationStatus;
  /** Tailwind classes for a pill badge (bg / text / border). */
  badgeClass: string;
  /** Solid tile colour used on the card icon when this status is active. */
  tileClass: string;
}

export const IMMUNIZATION_STATUSES: readonly ImmunizationStatusMeta[] = [
  {
    value: 'completed',
    badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    tileClass: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600',
  },
  {
    value: 'not-done',
    badgeClass: 'bg-amber-50 text-amber-700 border-amber-100',
    tileClass: 'bg-amber-50 dark:bg-amber-900/30 text-amber-600',
  },
  {
    value: 'entered-in-error',
    badgeClass: 'bg-red-50 text-red-700 border-red-100',
    tileClass: 'bg-red-50 dark:bg-red-900/30 text-red-600',
  },
] as const;

const BY_VALUE = new Map<ImmunizationStatus, ImmunizationStatusMeta>(
  IMMUNIZATION_STATUSES.map((m) => [m.value, m]),
);

export function getStatusMeta(
  status: string | null | undefined,
): ImmunizationStatusMeta {
  return (
    BY_VALUE.get((status as ImmunizationStatus) ?? 'completed') ??
    IMMUNIZATION_STATUSES[0]
  );
}
