/** OAuth2 client management service.
 *
 * Wraps the `/oauth/*` endpoints (RFC 6749 client-credentials + RFC 7009
 * revocation + admin client CRUD). The FHIR R4 facade is the public interop
 * surface; external systems authenticate via these clients.
 *
 * @see docs/API_LAYERS.md, docs/FHIR_R4_FACADE.md
 */
import api from '../api/axios';
import type {
  OAuthClient,
  OAuthClientCreate,
  OAuthClientUpdate,
  OAuthClientWithSecret,
} from '../types/oauth';

export const oauthService = {
  /** List OAuth clients in the caller's tenant (SYSTEM_ADMIN: all). */
  async list(): Promise<OAuthClient[]> {
    const r = await api.get<OAuthClient[]>('/oauth/clients');
    return r.data;
  },

  /** Register a client. Returns the plaintext secret ONCE (store immediately). */
  async create(payload: OAuthClientCreate): Promise<OAuthClientWithSecret> {
    const r = await api.post<OAuthClientWithSecret>('/oauth/clients', payload);
    return r.data;
  },

  /** Issue a new secret; the old hash is overwritten. Returns plaintext once. */
  async rotateSecret(id: string): Promise<{ client_id: string; client_secret: string }> {
    const r = await api.post(`/oauth/clients/${id}/rotate-secret`);
    return r.data;
  },

  /** Update metadata / scopes / active flag. Scope changes apply on next token. */
  async update(id: string, payload: OAuthClientUpdate): Promise<OAuthClient> {
    const r = await api.patch<OAuthClient>(`/oauth/clients/${id}`, payload);
    return r.data;
  },

  /** Hard-delete a client. Outstanding tokens stay valid until they expire. */
  async remove(id: string): Promise<void> {
    await api.delete(`/oauth/clients/${id}`);
  },
};
