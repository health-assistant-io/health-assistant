/** OAuth2 client (FHIR facade consumer) — frontend types.
 *
 * Mirrors `backend/app/schemas/oauth.py`. An OAuthClient is a registered
 * external system that authenticates against the FHIR R4 facade via the
 * OAuth2 client-credentials grant with SMART-on-FHIR scopes.
 */
export interface OAuthClient {
  id: string;
  client_id: string;
  tenant_id: string;
  display_name: string;
  scopes: string[];
  bound_patient_id: string | null;
  is_confidential: boolean;
  is_active: boolean;
  created_by_user_id: string | null;
}

/** Response to POST /oauth/clients — carries the plaintext secret exactly once. */
export interface OAuthClientWithSecret extends OAuthClient {
  client_secret: string;
}

export interface OAuthClientCreate {
  display_name: string;
  scopes: string[];
  tenant_id?: string;
  bound_patient_id?: string;
}

export interface OAuthClientUpdate {
  display_name?: string;
  scopes?: string[];
  is_active?: boolean;
  bound_patient_id?: string | null;
}

/** POST /oauth/token response. */
export interface OAuthTokenResponse {
  access_token: string;
  token_type: 'Bearer';
  expires_in: number;
  scope: string;
  tenant_id: string;
}
