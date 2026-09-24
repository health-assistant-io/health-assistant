import type { Patient } from '../types/patient';

export interface PatientAddress {
  line?: string[];
  city?: string;
  postalCode?: string;
  country?: string;
  text?: string;
}

export interface PatientAddressFormValue {
  line?: string;
  city?: string;
  postalCode?: string;
  country?: string;
  text?: string;
}

interface StoredPatientAddress extends Omit<PatientAddress, 'line'> {
  line?: string[] | string;
}

export function toPatientAddressFormValues(
  addresses: StoredPatientAddress[] | StoredPatientAddress | null | undefined,
): PatientAddressFormValue[] {
  const rows = Array.isArray(addresses) ? addresses : addresses ? [addresses] : [];
  if (!rows.length) return [{}];

  return rows.map(({ line, ...address }) => ({
    ...address,
    line: Array.isArray(line) ? line.join('\n') : line,
  }));
}

export function toPatientAddresses(
  addresses: PatientAddressFormValue[],
): PatientAddress[] {
  return addresses
    .map(({ line, city, postalCode, country, text }) => {
      const lines = (line ?? '').split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      const address: PatientAddress = {};
      if (lines.length) address.line = lines;
      if (city) address.city = city;
      if (postalCode) address.postalCode = postalCode;
      if (country) address.country = country;
      if (text) address.text = text;
      return address;
    })
    .filter((address) => Object.keys(address).length > 0);
}

export function formatPatientSaveError(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback;

  const candidate = error as {
    message?: unknown;
    response?: {
      status?: unknown;
      statusText?: unknown;
      data?: { detail?: unknown };
    };
  };
  const detail = candidate.response?.data?.detail;

  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown; msg?: unknown; message?: unknown };
    const location = Array.isArray(first?.loc) ? first.loc.join('.') : '';
    const message = first?.msg ?? first?.message;
    if (typeof message === 'string' && message) {
      return location ? `${location}: ${message}` : message;
    }
  }

  const { status, statusText } = candidate.response ?? {};
  if (status) {
    const description = statusText || candidate.message;
    return description ? `${status}: ${String(description)}` : String(status);
  }
  return typeof candidate.message === 'string' && candidate.message
    ? candidate.message
    : fallback;
}

export function patientAddresses(patient: Patient): StoredPatientAddress[] {
  const address = patient.address as StoredPatientAddress[] | StoredPatientAddress | null | undefined;
  return Array.isArray(address) ? address : address ? [address] : [];
}
