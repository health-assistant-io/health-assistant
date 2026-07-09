/**
 * Service wrapper for the vaccines endpoints (`/vaccines/*`).
 *
 * Mirrors the medicationService pattern: named-function exports, strongly
 * typed, unwraps the axios envelope at the service boundary.
 */
import api from '../api/axios';
import type {
  VaccineCatalogEntry,
  PatientImmunization,
  PatientImmunizationCreate,
  PatientImmunizationUpdate,
} from '../types/vaccine';

const BASE = '/vaccines';

// ── Catalog ──

export async function searchVaccineCatalog(
  search?: string,
): Promise<VaccineCatalogEntry[]> {
  const { data } = await api.get<VaccineCatalogEntry[]>(`${BASE}/catalog`, {
    params: { search },
  });
  return data;
}

export async function getVaccineCatalogEntry(
  catalogId: string,
): Promise<VaccineCatalogEntry> {
  const { data } = await api.get<VaccineCatalogEntry>(
    `${BASE}/catalog/${catalogId}`,
  );
  return data;
}

// ── Patient immunizations ──

export async function getPatientImmunizations(
  patientId: string,
): Promise<PatientImmunization[]> {
  const { data } = await api.get<PatientImmunization[]>(
    `${BASE}/patient/${patientId}`,
  );
  return data;
}

export async function addPatientImmunization(
  patientId: string,
  payload: PatientImmunizationCreate,
): Promise<PatientImmunization> {
  const { data } = await api.post<PatientImmunization>(
    `${BASE}/patient/${patientId}`,
    payload,
  );
  return data;
}

export async function getImmunization(
  immunizationId: string,
): Promise<PatientImmunization> {
  const { data } = await api.get<PatientImmunization>(
    `${BASE}/${immunizationId}`,
  );
  return data;
}

export async function updatePatientImmunization(
  immunizationId: string,
  payload: PatientImmunizationUpdate,
): Promise<PatientImmunization> {
  const { data } = await api.put<PatientImmunization>(
    `${BASE}/${immunizationId}`,
    payload,
  );
  return data;
}

export async function deletePatientImmunization(
  immunizationId: string,
): Promise<void> {
  await api.delete(`${BASE}/${immunizationId}`);
}
