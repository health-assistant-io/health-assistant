import type { Patient } from '../types/patient';

/**
 * FHIR R4 Address. The forms edit `line`, `city`, `postalCode`, `country` and
 * `text`; any other element (`use`, `type`, `state`, `district`, `period`,
 * `extension`, ...) is carried through a save untouched.
 */
export interface PatientAddress {
  line?: string[];
  city?: string;
  postalCode?: string;
  country?: string;
  text?: string;
  [element: string]: unknown;
}

export interface PatientAddressFormValue {
  line?: string;
  city?: string;
  postalCode?: string;
  country?: string;
  text?: string;
  [element: string]: unknown;
}

interface StoredPatientAddress extends Omit<PatientAddress, 'line'> {
  line?: string[] | string;
}

/** Elements that make an address worth keeping once the edited fields are cleaned. */
const ADDRESS_CONTENT = ['line', 'city', 'postalCode', 'country', 'text', 'state', 'district'];

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
    .map(({ line, city, postalCode, country, text, ...rest }) => {
      const address: PatientAddress = { ...rest };
      const lines = (line ?? '').split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
      if (lines.length) address.line = lines;
      for (const [key, value] of Object.entries({ city, postalCode, country, text })) {
        const trimmed = typeof value === 'string' ? value.trim() : '';
        if (trimmed) address[key] = trimmed;
      }
      return address;
    })
    .filter((address) => ADDRESS_CONTENT.some((key) => key in address));
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
