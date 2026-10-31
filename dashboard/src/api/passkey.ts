import { apiClient } from './client';

export type AuthenticatorTransport = 'ble' | 'hybrid' | 'internal' | 'nfc' | 'usb';
export type UserVerificationRequirement = 'discouraged' | 'preferred' | 'required';
export type AttestationConveyancePreference = 'direct' | 'enterprise' | 'indirect' | 'none';

export interface PasskeySummary {
  id: string;
  friendly_name: string;
  aaguid: string | null;
  created_at: string;
  last_used_at: string | null;
  backup_eligible: boolean;
}

// Utility: Base64URL string to ArrayBuffer / Uint8Array
export function base64UrlToBuffer(base64Url: string): ArrayBuffer {
  const padding = '='.repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const buffer = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    buffer[i] = rawData.charCodeAt(i);
  }
  return buffer.buffer;
}

// Utility: ArrayBuffer to Base64URL string
export function bufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

/** Check if WebAuthn is supported by the current browser */
export function isPasskeySupported(): boolean {
  return typeof window !== 'undefined' && !!window.PublicKeyCredential;
}

/** Check if Conditional UI (passkey autofill) is supported */
export async function isConditionalUiSupported(): Promise<boolean> {
  if (!isPasskeySupported()) return false;
  if (typeof PublicKeyCredential.isConditionalMediationAvailable === 'function') {
    return await PublicKeyCredential.isConditionalMediationAvailable();
  }
  return false;
}

/** Initiate passkey login ceremony */
export async function loginWithPasskey(
  email?: string,
  conditional = false
): Promise<{ success: boolean; redirect_url?: string }> {
  if (!isPasskeySupported()) {
    throw new Error('Passkeys are not supported in this browser.');
  }

  // 1. Fetch authentication options from backend
  const optionsRes = await apiClient.post<{
    options: PublicKeyCredentialRequestOptionsJSON;
    challenge_id: string;
  }>('/auth/passkey/login/options', { email: email || undefined }, { _skipApiKeyAuth: true });

  const { options, challenge_id } = optionsRes.data;

  // 2. Prepare PublicKeyCredentialRequestOptions
  const publicKey: PublicKeyCredentialRequestOptions = {
    challenge: base64UrlToBuffer(options.challenge),
    timeout: options.timeout || 60000,
    rpId: options.rpId,
    userVerification: options.userVerification || 'preferred',
  };

  if (options.allowCredentials && options.allowCredentials.length > 0) {
    publicKey.allowCredentials = options.allowCredentials.map(
      (c: { id: string; transports?: AuthenticatorTransport[] }) => ({
        id: base64UrlToBuffer(c.id),
        type: 'public-key',
        transports: c.transports,
      })
    );
  }

  // 3. Prompt user for biometric / hardware authenticator
  const credential = (await navigator.credentials.get({
    publicKey,
    mediation: conditional ? 'conditional' : 'optional',
  } as CredentialRequestOptions)) as PublicKeyCredential | null;

  if (!credential) {
    throw new Error('Authentication cancelled or no credential returned.');
  }

  const response = credential.response as AuthenticatorAssertionResponse;

  // 4. Send assertion back to backend for verification
  const verifyRes = await apiClient.post(
    '/auth/passkey/login/verify',
    {
      challenge_id,
      credential: {
        id: credential.id,
        rawId: bufferToBase64Url(credential.rawId),
        type: credential.type,
        response: {
          clientDataJSON: bufferToBase64Url(response.clientDataJSON),
          authenticatorData: bufferToBase64Url(response.authenticatorData),
          signature: bufferToBase64Url(response.signature),
          userHandle: response.userHandle ? bufferToBase64Url(response.userHandle) : null,
        },
      },
    },
    { _skipApiKeyAuth: true }
  );

  return verifyRes.data;
}

/** Initiate passkey registration ceremony for authenticated user */
export async function registerPasskey(friendlyName?: string): Promise<{ success: boolean }> {
  if (!isPasskeySupported()) {
    throw new Error('Passkeys are not supported in this browser.');
  }

  // 1. Fetch creation options
  const optionsRes = await apiClient.post<{
    options: PublicKeyCredentialCreationOptionsJSON;
    challenge_id: string;
  }>('/auth/passkey/register/options');

  const { options, challenge_id } = optionsRes.data;

  // 2. Prepare PublicKeyCredentialCreationOptions
  const publicKey: PublicKeyCredentialCreationOptions = {
    challenge: base64UrlToBuffer(options.challenge),
    rp: options.rp,
    user: {
      id: base64UrlToBuffer(options.user.id),
      name: options.user.name,
      displayName: options.user.displayName,
    },
    pubKeyCredParams: options.pubKeyCredParams.map((p: { type: string; alg: number }) => ({
      type: 'public-key',
      alg: p.alg,
    })),
    timeout: options.timeout || 60000,
    attestation: options.attestation || 'none',
    authenticatorSelection: options.authenticatorSelection,
  };

  if (options.excludeCredentials && options.excludeCredentials.length > 0) {
    publicKey.excludeCredentials = options.excludeCredentials.map(
      (c: { id: string; transports?: AuthenticatorTransport[] }) => ({
        id: base64UrlToBuffer(c.id),
        type: 'public-key',
        transports: c.transports,
      })
    );
  }

  // 3. Prompt user for registration
  const credential = (await navigator.credentials.create({
    publicKey,
  })) as PublicKeyCredential | null;

  if (!credential) {
    throw new Error('Registration cancelled or no credential returned.');
  }

  const response = credential.response as AuthenticatorAttestationResponse;

  // 4. Send response to backend for verification
  const verifyRes = await apiClient.post('/auth/passkey/register/verify', {
    challenge_id,
    credential: {
      id: credential.id,
      rawId: bufferToBase64Url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: bufferToBase64Url(response.clientDataJSON),
        attestationObject: bufferToBase64Url(response.attestationObject),
        transports: response.getTransports ? response.getTransports() : undefined,
      },
    },
    friendly_name: friendlyName || 'Passkey',
  });

  return verifyRes.data;
}

/** List registered passkeys for current user */
export async function listPasskeys(): Promise<PasskeySummary[]> {
  const res = await apiClient.get<PasskeySummary[]>('/auth/passkey/list');
  return res.data;
}

/** Rename a registered passkey */
export async function renamePasskey(id: string, friendlyName: string): Promise<void> {
  await apiClient.patch(`/auth/passkey/${id}`, { friendly_name: friendlyName });
}

/** Revoke a registered passkey */
export async function deletePasskey(id: string): Promise<void> {
  await apiClient.delete(`/auth/passkey/${id}`);
}

// Minimal JSON option types matching backend output
interface PublicKeyCredentialRequestOptionsJSON {
  challenge: string;
  timeout?: number;
  rpId: string;
  userVerification?: UserVerificationRequirement;
  allowCredentials?: Array<{ id: string; type: string; transports?: AuthenticatorTransport[] }>;
}

interface PublicKeyCredentialCreationOptionsJSON {
  challenge: string;
  rp: { id: string; name: string };
  user: { id: string; name: string; displayName: string };
  pubKeyCredParams: Array<{ type: string; alg: number }>;
  timeout?: number;
  attestation?: AttestationConveyancePreference;
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  excludeCredentials?: Array<{ id: string; type: string; transports?: AuthenticatorTransport[] }>;
}
