export interface ConsumedInvitation {
  token: string;
  hadToken: boolean;
}

interface InvitationLocation {
  href: string;
}

interface InvitationHistory {
  state: unknown;
  replaceState: (data: unknown, unused: string, url?: string | URL | null) => void;
}

/**
 * Consume a bearer token once, preferring the fragment used by new emails,
 * and replace the current history entry with a token-free URL.
 */
export function consumeInvitationToken(
  location: InvitationLocation,
  history: InvitationHistory
): ConsumedInvitation {
  const url = new URL(location.href);
  const fragment = url.hash.startsWith('#') ? url.hash.slice(1) : url.hash;
  const fragmentToken = new URLSearchParams(fragment).get('token');
  const queryToken = url.searchParams.get('token');
  const token = (fragmentToken || queryToken || '').trim();
  const hadToken = fragmentToken !== null || queryToken !== null;

  if (hadToken || url.hash) {
    url.searchParams.delete('token');
    url.hash = '';
    history.replaceState(history.state, '', `${url.pathname}${url.search}`);
  }

  return { token, hadToken };
}
