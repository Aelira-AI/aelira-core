import { apiClient } from './client';
import type { DeadlineInfo } from '../types/deadline';

export type RegulatoryFramework =
  | 'US_ADA_TITLE_II'
  | 'EU_EAA'
  | 'UK_PSBAR'
  | 'CA_AODA'
  | 'AU_DDA'
  | 'NONE';

export type TitleIIEntityClass = 'large' | 'small_or_special_district';

export interface SupportedRegulatoryFramework {
  code: RegulatoryFramework;
  name: string;
  default_country_code: string | null;
  requires_explicit_selection: boolean;
  requires_title_ii_entity_class: boolean;
  allows_custom_deadline: boolean;
}

export interface RegulatoryProfile {
  schema_version: 1;
  profile_revision: number;
  country_code: string | null;
  // Legacy rows may contain a retired or not-yet-implemented code. The
  // selectable options below remain the only values accepted by PUT.
  regulatory_framework: string | null;
  title_ii_entity_class: TitleIIEntityClass | null;
  custom_deadline: string | null;
  custom_deadline_verified: boolean;
  configuration_complete: boolean;
  deadline: DeadlineInfo;
  supported_frameworks: SupportedRegulatoryFramework[];
}

export interface RegulatoryProfileUpdate {
  country_code: string | null;
  regulatory_framework: RegulatoryFramework | null;
  title_ii_entity_class: TitleIIEntityClass | null;
  custom_deadline: string | null;
  custom_deadline_verified: boolean;
  expected_revision: number;
}

const endpoint = '/admin/regulatory-profile';

export const regulatoryProfileApi = {
  get: async (): Promise<RegulatoryProfile> => {
    const response = await apiClient.get<RegulatoryProfile>(endpoint);
    return response.data;
  },

  update: async (profile: RegulatoryProfileUpdate): Promise<RegulatoryProfile> => {
    const response = await apiClient.put<RegulatoryProfile>(endpoint, profile);
    return response.data;
  },
};
