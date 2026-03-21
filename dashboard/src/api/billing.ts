/**
 * Billing API client for Stripe tier upgrades.
 *
 * Supports regional pricing:
 * - 'us': USD prices (default)
 * - 'au': AUD prices (1.5x multiplier)
 */
import { apiClient } from './client';
import type { Tier } from '../types';

// ============================================================================
// Types
// ============================================================================

export type BillingRegion = 'us' | 'au';
export type BillingPeriod = 'monthly' | 'yearly';
export type UpgradeTier = 'individual_plus' | 'individual_pro';
export type SubscriptionStatus = 'active' | 'past_due' | 'canceled' | 'incomplete' | 'trialing' | null;

export interface CheckoutSessionRequest {
  target_tier: UpgradeTier;
  billing_period: BillingPeriod;
  region: BillingRegion;
}

export interface CheckoutSessionResponse {
  checkout_url: string;
  session_id: string;
}

export interface SubscriptionStatusResponse {
  tier: Tier;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  subscription_status: SubscriptionStatus;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

export interface BillingPortalResponse {
  portal_url: string;
}

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Detect user's region from browser or default to 'us'.
 * In production, this should be set from the user's profile or URL.
 */
const detectRegion = (): BillingRegion => {
  // Check URL path for region (e.g., /au/settings)
  const pathMatch = window.location.pathname.match(/^\/(us|au)\//);
  if (pathMatch) return pathMatch[1] as BillingRegion;

  // Check localStorage for saved preference
  const savedRegion = localStorage.getItem('aelira_region');
  if (savedRegion && ['us', 'au'].includes(savedRegion)) return savedRegion as BillingRegion;

  // Check browser timezone/locale for Australia
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (timezone?.includes('Australia')) return 'au';

  // Default to US
  return 'us';
};

// ============================================================================
// API Methods
// ============================================================================

export const billingApi = {
  /**
   * Create a Stripe Checkout session for tier upgrade.
   * @param targetTier - 'individual_plus' or 'individual_pro'
   * @param billingPeriod - 'monthly' or 'yearly'
   * @param region - 'us' or 'au' (optional, auto-detected if not provided)
   */
  createCheckoutSession: async (
    targetTier: UpgradeTier,
    billingPeriod: BillingPeriod = 'monthly',
    region: BillingRegion | null = null
  ): Promise<CheckoutSessionResponse> => {
    const response = await apiClient.post<CheckoutSessionResponse>('/billing/create-checkout-session', {
      target_tier: targetTier,
      billing_period: billingPeriod,
      region: region || detectRegion(),
    });
    return response.data;
  },

  /**
   * Get current subscription status.
   */
  getSubscriptionStatus: async (): Promise<SubscriptionStatusResponse> => {
    const response = await apiClient.get<SubscriptionStatusResponse>('/billing/subscription');
    return response.data;
  },

  /**
   * Create a Stripe Billing Portal session for subscription management.
   */
  createBillingPortalSession: async (): Promise<BillingPortalResponse> => {
    const response = await apiClient.post<BillingPortalResponse>('/billing/portal');
    return response.data;
  },

  /**
   * Utility to detect the user's region.
   */
  detectRegion,
};

export default billingApi;
