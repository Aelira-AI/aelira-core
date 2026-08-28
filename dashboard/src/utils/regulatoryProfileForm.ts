import type { AuthMethod } from '../context/auth-context';
import type { UserRole } from '../types';
import type {
  RegulatoryFramework,
  RegulatoryProfile,
  RegulatoryProfileUpdate,
  TitleIIEntityClass,
} from '../api/regulatoryProfile';

export interface RegulatoryProfileForm {
  country_code: string;
  regulatory_framework: RegulatoryFramework | '';
  title_ii_entity_class: TitleIIEntityClass | '';
  custom_deadline: string;
  custom_deadline_verified: boolean;
}

export type RegulatoryProfileField = keyof RegulatoryProfileForm;
export type RegulatoryProfileFieldErrors = Partial<Record<RegulatoryProfileField, string>>;

export type RegulatoryProfileFailure =
  | { kind: 'conflict'; current: RegulatoryProfile; message: string }
  | { kind: 'validation'; field?: RegulatoryProfileField; message: string }
  | { kind: 'generic'; message: string };

export function canManageRegulatoryProfile(
  authMethod: AuthMethod,
  role: UserRole | undefined,
): boolean {
  return authMethod !== 'lti' && (role === 'admin' || role === 'super_admin');
}

export function isSupportedRegulatoryFramework(
  profile: RegulatoryProfile,
  code: string | null,
): code is RegulatoryFramework {
  return Boolean(code && profile.supported_frameworks.some((option) => option.code === code));
}

export function unsupportedCurrentFramework(profile: RegulatoryProfile): string | null {
  return profile.regulatory_framework
    && !isSupportedRegulatoryFramework(profile, profile.regulatory_framework)
    ? profile.regulatory_framework
    : null;
}

export function editableRegulatoryProfile(profile: RegulatoryProfile): RegulatoryProfileForm {
  const framework = isSupportedRegulatoryFramework(profile, profile.regulatory_framework)
    ? profile.regulatory_framework
    : '';
  const metadata = profile.supported_frameworks.find(({ code }) => code === framework);
  const allowsCustomDeadline = metadata?.allows_custom_deadline ?? false;

  return {
    country_code: profile.country_code ?? '',
    regulatory_framework: framework,
    title_ii_entity_class: framework === 'US_ADA_TITLE_II'
      ? profile.title_ii_entity_class ?? ''
      : '',
    custom_deadline: allowsCustomDeadline ? profile.custom_deadline ?? '' : '',
    custom_deadline_verified: allowsCustomDeadline && profile.custom_deadline_verified,
  };
}

export function validateRegulatoryProfileForm(
  form: RegulatoryProfileForm,
): RegulatoryProfileFieldErrors {
  const errors: RegulatoryProfileFieldErrors = {};
  if (!/^[A-Z]{2}$/.test(form.country_code)) {
    errors.country_code = 'Enter a valid two-letter country code, such as US, GB, CA, or AU.';
  }
  if (!form.regulatory_framework) {
    errors.regulatory_framework = 'Select the framework that your institution has verified applies.';
  }
  if (form.regulatory_framework === 'US_ADA_TITLE_II' && !form.title_ii_entity_class) {
    errors.title_ii_entity_class = 'Select the Title II entity class verified by your institution.';
  }
  if (form.custom_deadline && !form.custom_deadline_verified) {
    errors.custom_deadline_verified = 'Confirm that your institution has verified this custom deadline.';
  }
  return errors;
}

export function withChangedLegalContext(
  form: RegulatoryProfileForm,
  changes: Pick<RegulatoryProfileForm, 'country_code'>
    | Pick<RegulatoryProfileForm, 'regulatory_framework' | 'title_ii_entity_class'>
    | Pick<RegulatoryProfileForm, 'title_ii_entity_class'>,
): RegulatoryProfileForm {
  return {
    ...form,
    ...changes,
    custom_deadline: '',
    custom_deadline_verified: false,
  };
}

export function regulatoryProfileUpdate(
  profile: RegulatoryProfile,
  form: RegulatoryProfileForm,
): RegulatoryProfileUpdate {
  const metadata = profile.supported_frameworks.find(
    ({ code }) => code === form.regulatory_framework,
  );
  const customDeadline = metadata?.allows_custom_deadline ? form.custom_deadline || null : null;

  return {
    country_code: form.country_code || null,
    regulatory_framework: form.regulatory_framework || null,
    title_ii_entity_class: form.regulatory_framework === 'US_ADA_TITLE_II'
      ? form.title_ii_entity_class || null
      : null,
    custom_deadline: customDeadline,
    custom_deadline_verified: Boolean(customDeadline && form.custom_deadline_verified),
    expected_revision: profile.profile_revision,
  };
}

export function clearedRegulatoryProfile(profile: RegulatoryProfile): RegulatoryProfileUpdate {
  return {
    country_code: null,
    regulatory_framework: null,
    title_ii_entity_class: null,
    custom_deadline: null,
    custom_deadline_verified: false,
    expected_revision: profile.profile_revision,
  };
}

export function classifyRegulatoryProfileFailure(caught: unknown): RegulatoryProfileFailure {
  const failure = caught as {
    response?: {
      data?: {
        detail?: {
          code?: string;
          field?: string;
          message?: string;
          current?: RegulatoryProfile;
        } | Array<{ loc?: Array<string | number>; msg?: string }>;
      };
    };
  };
  const detail = failure.response?.data?.detail;

  if (!Array.isArray(detail)
    && detail?.code === 'regulatory_profile_revision_conflict'
    && detail.current) {
    return {
      kind: 'conflict',
      current: detail.current,
      message: 'Another administrator changed this profile. Review the current values before saving again.',
    };
  }
  if (!Array.isArray(detail) && detail?.code === 'invalid_regulatory_profile') {
    return {
      kind: 'validation',
      field: typeof detail.field === 'string' ? detail.field as RegulatoryProfileField : undefined,
      message: detail.message ?? 'The profile is not valid. Review the highlighted fields.',
    };
  }
  if (Array.isArray(detail)) {
    const rawField = detail[0]?.loc?.at(-1);
    return {
      kind: 'validation',
      field: typeof rawField === 'string' ? rawField as RegulatoryProfileField : undefined,
      message: detail[0]?.msg ?? 'The profile is not valid. Review the highlighted fields.',
    };
  }
  return { kind: 'generic', message: 'The regulatory profile could not be saved. Try again.' };
}
