// The API requires a CSRF token on every cookie-authenticated request that
// changes state, so a session cookie alone cannot create an API key. Fetch the
// token with the session first, then send it as both the cookie and the header.
export async function createApiKeyFromSession(apiUrl: string, accessCookie: string, name: string): Promise<Response> {
  const session = `aelira_access=${accessCookie}`
  const tokenResponse = await fetch(`${apiUrl}/api/csrf-token`, { headers: { Cookie: session } })
  if (!tokenResponse.ok) return tokenResponse

  const { csrf_token: csrfToken } = await tokenResponse.json() as { csrf_token?: unknown }
  if (typeof csrfToken !== 'string' || csrfToken.length === 0) {
    throw new Error('The API did not return a CSRF token.')
  }

  return fetch(`${apiUrl}/auth/keys`, {
    body: JSON.stringify({ name }),
    headers: {
      'Content-Type': 'application/json',
      'Cookie': `${session}; csrf_token=${csrfToken}`,
      'X-CSRF-Token': csrfToken,
    },
    method: 'POST',
  })
}
