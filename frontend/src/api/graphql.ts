import { GraphQLClient } from 'graphql-request';

const GRAPHQL_ENDPOINT = import.meta.env.VITE_GRAPHQL_URL || '/graphql';

export const graphqlClient = new GraphQLClient(GRAPHQL_ENDPOINT, {
  headers: {
    'Content-Type': 'application/json',
  },
});

export async function getAccessToken() {
  const token = localStorage.getItem('accessToken');
  return token;
}

graphqlClient.setHeader('Authorization', `Bearer ${await getAccessToken()}`);