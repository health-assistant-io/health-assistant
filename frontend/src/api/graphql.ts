import { GraphQLClient } from 'graphql-request';

const GRAPHQL_ENDPOINT = import.meta.env.VITE_GRAPHQL_URL || '/graphql';

/**
 * Read the current access token from localStorage.
 *
 * Audit A13: previously the module called
 * ``graphqlClient.setHeader('Authorization', `Bearer ${await getAccessToken()}`)``
 * exactly once at module-load time. The header was therefore captured
 * once at first import and never refreshed on login or token rotation —
 * every GraphQL call 401'd after the first token expired, with no recovery.
 *
 * The fix is per-request token injection. We expose {@link graphqlRequest}
 * as the canonical entry point so callers always send the live token.
 */
export async function getAccessToken(): Promise<string | null> {
  return localStorage.getItem('accessToken');
}

/** Shared client instance — no Authorization header is set on the client itself. */
export const graphqlClient = new GraphQLClient(GRAPHQL_ENDPOINT, {
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Execute a GraphQL request with the *current* access token attached.
 *
 * Use this instead of ``graphqlClient.request`` directly to ensure token
 * rotation is always respected.
 */
export async function graphqlRequest<T = unknown>(
  query: string,
  variables?: Record<string, unknown>,
): Promise<T> {
  const token = await getAccessToken();
  if (token) {
    graphqlClient.setHeader('Authorization', `Bearer ${token}`);
  } else {
    graphqlClient.setHeader('Authorization', '');
  }
  return graphqlClient.request<T>(query, variables);
}
