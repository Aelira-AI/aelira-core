interface NoticeUser {
  has_api_keys?: boolean;
}

/**
 * The "dashboard API keys have been retired" notice is for a signed-in session
 * whose account owns, or once owned, programmatic keys. A new account has no
 * key to migrate, and the server reports nothing for it, so it sees no notice.
 */
export function shouldShowApiKeyRetirementNotice(
  authMethod: string | null,
  user: NoticeUser | null,
): boolean {
  return authMethod === 'session' && user !== null && user.has_api_keys === true;
}
