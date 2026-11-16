/**
 * Medication status vocabulary at the frontend boundary.
 *
 * The internal REST API uses the database enum (uppercase, underscore:
 * `ACTIVE`, `ON_HOLD`, `ENTERED_IN_ERROR`); the UI works with the FHIR-style
 * spelling (`active`, `on-hold`, `entered-in-error`). All translation lives
 * here and in `medicationService`, so components never compare raw wire
 * values.
 */
export type MedicationStatus =
  | 'active'
  | 'inactive'
  | 'completed'
  | 'cancelled'
  | 'entered-in-error'
  | 'intended'
  | 'stopped'
  | 'on-hold'
  | 'unknown';

const TO_CLIENT: Record<string, MedicationStatus> = {
  ACTIVE: 'active',
  INACTIVE: 'inactive',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
  ENTERED_IN_ERROR: 'entered-in-error',
  INTENDED: 'intended',
  STOPPED: 'stopped',
  ON_HOLD: 'on-hold',
  UNKNOWN: 'unknown',
};

export function normalizeMedicationStatus(
  value: string | null | undefined,
): MedicationStatus {
  const key = String(value ?? '')
    .trim()
    .toUpperCase()
    .replace(/-/g, '_');
  return TO_CLIENT[key] ?? 'unknown';
}

export function toApiMedicationStatus(value: string | null | undefined): string | undefined {
  if (value == null || value === '') return undefined;
  return value.trim().toUpperCase().replace(/-/g, '_');
}

export function isActiveMedication(status: string | null | undefined): boolean {
  return normalizeMedicationStatus(status) === 'active';
}
