import { GraphQLClient } from 'graphql-request';

const GRAPHQL_ENDPOINT = import.meta.env.VITE_GRAPHQL_URL || '/graphql';

/**
 * Read the current access token from localStorage.
 *
 * The token is read at request time (not module-load time) via the
 * {@link graphqlRequest} wrapper, so login + token rotation are always
 * reflected in outgoing requests.
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
