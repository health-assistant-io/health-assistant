/**
 * Patient type — mirrors the ORM-shape `to_dict()` output returned by
 * the /patients/* domain endpoints. NOT canonical FHIR R4.
 *
 * The backend stores FHIR-enhanced relational rows and serializes them via
 * `to_dict()` (snake_case + app-specific fields like `user_id`, `mrn`,
 * `dashboard_layout`). This interface mirrors that runtime shape — NOT the
 * FHIR R4B camelCase shape (which is only produced by the /fhir/R4/* facade
 * and the export/import path).
 */

export interface Patient {
  id: string;
  tenant_id?: string;
  user_id?: string | null;
  name: { family: string; given: string[] } | { text: string } | any;
  gender: string;
  birth_date?: string;
  age?: number | null;
  deceased_boolean?: boolean | null;
  deceased_datetime?: string | null;
  address?: any;
  telecom?: any;
  mrn?: string;
  emergency_contact?: any;
  dashboard_layout?: any;
}
