import { FormEvent, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { AlertTriangle, CalendarDays, CheckCircle, Loader2, RefreshCw } from 'lucide-react';
import { useAuth } from '../../context/auth-context';
import {
  regulatoryProfileApi,
  type RegulatoryFramework,
  type RegulatoryProfile,
  type RegulatoryProfileUpdate,
  type TitleIIEntityClass,
} from '../../api/regulatoryProfile';
import {
  canManageRegulatoryProfile,
  classifyRegulatoryProfileFailure,
  clearedRegulatoryProfile,
  editableRegulatoryProfile,
  regulatoryProfileUpdate,
  unsupportedCurrentFramework,
  validateRegulatoryProfileForm,
  withChangedLegalContext,
  type RegulatoryProfileField,
  type RegulatoryProfileFieldErrors,
  type RegulatoryProfileForm,
} from '../../utils/regulatoryProfileForm';

function EditableRegulatoryProfileCard(): ReactElement {
  const [profile, setProfile] = useState<RegulatoryProfile | null>(null);
  const [form, setForm] = useState<RegulatoryProfileForm | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<RegulatoryProfileFieldErrors>({});
  const [status, setStatus] = useState('');
  const [conflict, setConflict] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const errorSummaryRef = useRef<HTMLParagraphElement>(null);
  const fieldRefs = useRef<Partial<Record<RegulatoryProfileField, HTMLInputElement | HTMLSelectElement | null>>>({});
  const pendingFocusRef = useRef<string | null>(null);

  const load = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    setFieldErrors({});
    setConflict(false);
    setConfirmClear(false);
    try {
      const current = await regulatoryProfileApi.get();
      setProfile(current);
      setForm(editableRegulatoryProfile(current));
      setStatus(`Regulatory profile loaded at revision ${current.profile_revision}.`);
    } catch {
      setError('The regulatory profile could not be loaded. Try again.');
    } finally {
      setLoading(false);
    }
  };

  // The privileged component is only mounted after the outer access guard.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, []);

  const selectedFramework = useMemo(
    () => profile?.supported_frameworks.find(({ code }) => code === form?.regulatory_framework) ?? null,
    [form?.regulatory_framework, profile?.supported_frameworks],
  );
  const isDirty = Boolean(profile && form && JSON.stringify(editableRegulatoryProfile(profile)) !== JSON.stringify(form));
  const unsupportedFramework = profile ? unsupportedCurrentFramework(profile) : null;
  const hasPersistedProfile = Boolean(profile && (
    profile.country_code
    || profile.regulatory_framework
    || profile.title_ii_entity_class
    || profile.custom_deadline
  ));

  const queueFailureFocus = (field?: string): void => {
    pendingFocusRef.current = field ?? '';
  };

  useEffect(() => {
    if (saving || pendingFocusRef.current === null) return;
    const field = pendingFocusRef.current;
    pendingFocusRef.current = null;
    window.setTimeout(() => {
      const target = field ? fieldRefs.current[field as RegulatoryProfileField] : null;
      (target ?? errorSummaryRef.current)?.focus();
    }, 0);
  }, [error, fieldErrors, saving]);

  const persist = async (update: RegulatoryProfileUpdate): Promise<void> => {
    setSaving(true);
    setError(null);
    setFieldErrors({});
    setConflict(false);
    setConfirmClear(false);

    try {
      const current = await regulatoryProfileApi.update(update);
      setProfile(current);
      setForm(editableRegulatoryProfile(current));
      setStatus(`Regulatory profile saved at revision ${current.profile_revision}. Dashboard and LMS deadline views now use this canonical result.`);
    } catch (caught) {
      const failure = classifyRegulatoryProfileFailure(caught);
      if (failure.kind === 'conflict') {
        setProfile(failure.current);
        setForm(editableRegulatoryProfile(failure.current));
        setConflict(true);
        setError(failure.message);
        setStatus(`Current profile loaded at revision ${failure.current.profile_revision}.`);
        queueFailureFocus();
      } else if (failure.kind === 'validation') {
        if (failure.field && form && failure.field in form) {
          setFieldErrors({ [failure.field]: failure.message });
        }
        setError(failure.message);
        queueFailureFocus(failure.field);
      } else {
        setError(failure.message);
        queueFailureFocus();
      }
    } finally {
      setSaving(false);
    }
  };

  const save = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    if (!profile || !form) return;

    const validation = validateRegulatoryProfileForm(form);
    if (Object.keys(validation).length > 0) {
      setFieldErrors(validation);
      setError('Review the highlighted fields before saving.');
      queueFailureFocus(Object.keys(validation)[0]);
      return;
    }
    await persist(regulatoryProfileUpdate(profile, form));
  };

  if (loading) {
    return <section id="regulatory-profile" className="card mb-6 p-6" aria-label="Regulatory deadline profile" aria-busy="true"><span className="inline-flex items-center gap-2"><Loader2 className="w-5 h-5 animate-spin" aria-hidden="true" />Loading regulatory profile…</span></section>;
  }
  if (!profile || !form) {
    return <section id="regulatory-profile" className="card mb-6 p-6" aria-labelledby="regulatory-profile-title"><h2 id="regulatory-profile-title" className="text-xl font-semibold text-primary">Regulatory deadline profile</h2><p ref={errorSummaryRef} tabIndex={-1} role="alert" className="mt-3 text-sm" style={{ color: 'var(--content-error)' }}>{error}</p><button type="button" className="btn-secondary mt-3" onClick={() => void load()}>Retry</button></section>;
  }

  const showTitleIIClass = form.regulatory_framework === 'US_ADA_TITLE_II';
  const allowCustomDeadline = selectedFramework?.allows_custom_deadline ?? false;

  return (
    <section id="regulatory-profile" className="card mb-6 scroll-mt-6" aria-labelledby="regulatory-profile-title">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <div className="flex items-center gap-2"><CalendarDays className="w-5 h-5" aria-hidden="true" /><h2 id="regulatory-profile-title" className="text-xl font-semibold text-primary">Regulatory deadline profile</h2></div>
        <p id="regulatory-profile-description" className="text-sm text-tertiary mt-1">Choose only the framework your institution has verified applies. Aelira will not infer a narrower legal deadline.</p>
      </div>
      <div className="px-6 py-5">
        <div aria-live="polite" className="sr-only">{status}</div>
        {status && <p className="text-sm mb-4" style={{ color: 'var(--content-success)' }}><CheckCircle className="w-4 h-4 inline mr-1" aria-hidden="true" />{status}</p>}
        {error && <p ref={errorSummaryRef} tabIndex={-1} role="alert" aria-live="assertive" className="text-sm mb-4" style={{ color: 'var(--content-error)' }}><AlertTriangle className="w-4 h-4 inline mr-1" aria-hidden="true" />{error}</p>}
        {unsupportedFramework && <div role="status" className="rounded-lg border p-4 mb-5" style={{ borderColor: 'var(--feature-warning-border)', backgroundColor: 'var(--feature-warning-surface)' }}><p className="font-medium text-primary">The saved framework {unsupportedFramework} is not available in this version.</p><p className="text-sm text-secondary mt-1">Select a supported framework or clear the profile. Aelira will not publish a deadline from the unsupported value.</p></div>}
        {profile.custom_deadline && !profile.custom_deadline_verified && <div role="status" className="rounded-lg border p-4 mb-5" style={{ borderColor: 'var(--feature-warning-border)', backgroundColor: 'var(--feature-warning-surface)' }}><p className="font-medium text-primary">The saved custom date needs verification.</p><p className="text-sm text-secondary mt-1">Re-enter and attest to the date, or clear the profile. Until then, canonical views ignore this custom date.</p></div>}

        <form onSubmit={save} aria-describedby="regulatory-profile-description" noValidate>
          <fieldset disabled={saving} className="space-y-5"><legend className="sr-only">Institution regulatory profile</legend>
            <div>
              <label htmlFor="regulatory-profile-country-code" className="block text-sm font-medium text-primary">Country code</label>
              <p id="regulatory-profile-country-code-help" className="text-sm text-tertiary">Two-letter ISO country code.</p>
              <input ref={(node) => { fieldRefs.current.country_code = node; }} id="regulatory-profile-country-code" className="input mt-2 w-full max-w-xs uppercase" value={form.country_code} maxLength={2} autoComplete="off" aria-invalid={Boolean(fieldErrors.country_code)} aria-describedby={`regulatory-profile-country-code-help${fieldErrors.country_code ? ' regulatory-profile-country-code-error' : ''}`} onChange={(event) => setForm(withChangedLegalContext(form, { country_code: event.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2) }))} />
              {fieldErrors.country_code && <p id="regulatory-profile-country-code-error" className="text-sm mt-1" style={{ color: 'var(--content-error)' }}>{fieldErrors.country_code}</p>}
            </div>

            <div>
              <label htmlFor="regulatory-profile-regulatory-framework" className="block text-sm font-medium text-primary">Regulatory framework</label>
              <p id="regulatory-profile-regulatory-framework-help" className="text-sm text-tertiary">Only implemented frameworks are available.</p>
              <select ref={(node) => { fieldRefs.current.regulatory_framework = node; }} id="regulatory-profile-regulatory-framework" className="input mt-2 w-full max-w-lg" value={form.regulatory_framework} aria-invalid={Boolean(fieldErrors.regulatory_framework)} aria-describedby={`regulatory-profile-regulatory-framework-help${fieldErrors.regulatory_framework ? ' regulatory-profile-regulatory-framework-error' : ''}`} onChange={(event) => {
                const regulatory_framework = event.target.value as RegulatoryFramework | '';
                setForm(withChangedLegalContext(form, { regulatory_framework, title_ii_entity_class: '' }));
              }}><option value="">Select a verified framework</option>{profile.supported_frameworks.map((framework) => <option key={framework.code} value={framework.code}>{framework.name}</option>)}</select>
              {fieldErrors.regulatory_framework && <p id="regulatory-profile-regulatory-framework-error" className="text-sm mt-1" style={{ color: 'var(--content-error)' }}>{fieldErrors.regulatory_framework}</p>}
            </div>

            {showTitleIIClass && <div>
              <label htmlFor="regulatory-profile-title-ii-entity-class" className="block text-sm font-medium text-primary">Title II entity class</label>
              <p id="regulatory-profile-title-ii-entity-class-help" className="text-sm text-tertiary">Select the classification your institution has verified.</p>
              <select ref={(node) => { fieldRefs.current.title_ii_entity_class = node; }} id="regulatory-profile-title-ii-entity-class" className="input mt-2 w-full max-w-lg" value={form.title_ii_entity_class} aria-invalid={Boolean(fieldErrors.title_ii_entity_class)} aria-describedby={`regulatory-profile-title-ii-entity-class-help${fieldErrors.title_ii_entity_class ? ' regulatory-profile-title-ii-entity-class-error' : ''}`} onChange={(event) => setForm(withChangedLegalContext(form, { title_ii_entity_class: event.target.value as TitleIIEntityClass | '' }))}><option value="">Select an entity class</option><option value="large">Large public entity</option><option value="small_or_special_district">Small public entity or special district</option></select>
              {fieldErrors.title_ii_entity_class && <p id="regulatory-profile-title-ii-entity-class-error" className="text-sm mt-1" style={{ color: 'var(--content-error)' }}>{fieldErrors.title_ii_entity_class}</p>}
            </div>}

            {allowCustomDeadline && <div>
              <label htmlFor="regulatory-profile-custom-deadline" className="block text-sm font-medium text-primary">Verified custom deadline <span className="font-normal text-tertiary">(optional)</span></label>
              <p id="regulatory-profile-custom-deadline-help" className="text-sm text-tertiary">Use a date only when your institution has independently verified it.</p>
              <input ref={(node) => { fieldRefs.current.custom_deadline = node; }} id="regulatory-profile-custom-deadline" type="date" className="input mt-2 w-full max-w-xs" value={form.custom_deadline} aria-invalid={Boolean(fieldErrors.custom_deadline)} aria-describedby={`regulatory-profile-custom-deadline-help${fieldErrors.custom_deadline ? ' regulatory-profile-custom-deadline-error' : ''}`} onChange={(event) => setForm({ ...form, custom_deadline: event.target.value, custom_deadline_verified: event.target.value ? form.custom_deadline_verified : false })} />
              {fieldErrors.custom_deadline && <p id="regulatory-profile-custom-deadline-error" className="text-sm mt-1" style={{ color: 'var(--content-error)' }}>{fieldErrors.custom_deadline}</p>}
              {form.custom_deadline && <label htmlFor="custom-deadline-verified" className="flex items-start gap-2 mt-3"><input ref={(node) => { fieldRefs.current.custom_deadline_verified = node; }} id="custom-deadline-verified" type="checkbox" checked={form.custom_deadline_verified} aria-invalid={Boolean(fieldErrors.custom_deadline_verified)} aria-describedby={`custom-deadline-verified-help${fieldErrors.custom_deadline_verified ? ' custom-deadline-verified-error' : ''}`} onChange={(event) => setForm({ ...form, custom_deadline_verified: event.target.checked })} /><span>I confirm this custom deadline has been verified by my institution.<span id="custom-deadline-verified-help" className="block text-sm text-tertiary">Aelira records this attestation with the profile change.</span></span></label>}
              {fieldErrors.custom_deadline_verified && <p id="custom-deadline-verified-error" className="text-sm mt-1" style={{ color: 'var(--content-error)' }}>{fieldErrors.custom_deadline_verified}</p>}
            </div>}

            <div className="rounded-lg border p-4" style={{ borderColor: 'var(--border-primary)', backgroundColor: 'var(--surface-secondary)' }}>
              <h3 className="font-semibold text-primary">Canonical deadline</h3>
              <p className="text-sm text-secondary mt-1">{profile.deadline.message}</p>
              {profile.deadline.deadline_label && <p className="text-sm font-medium text-primary mt-2">{profile.deadline.deadline_label}</p>}
              {isDirty && <p className="text-xs text-tertiary mt-2">Save the profile to calculate a new canonical result.</p>}
            </div>

            <div className="flex flex-wrap gap-3"><button type="submit" className="btn-primary disabled:cursor-not-allowed disabled:opacity-50" disabled={!isDirty || saving}>{saving ? 'Saving…' : 'Save regulatory profile'}</button><button type="button" className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" disabled={!isDirty || saving} onClick={() => { setForm(editableRegulatoryProfile(profile)); setFieldErrors({}); setError(null); setConflict(false); setConfirmClear(false); setStatus('Unsaved changes reset.'); }}>Reset changes</button>{conflict && <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => { setForm(editableRegulatoryProfile(profile)); setFieldErrors({}); setError(null); setConflict(false); setStatus(`Current profile reloaded at revision ${profile.profile_revision}.`); }}><RefreshCw className="w-4 h-4" aria-hidden="true" />Reload current profile</button>}{hasPersistedProfile && !confirmClear && <button type="button" className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" disabled={saving} onClick={() => setConfirmClear(true)}>Clear profile</button>}</div>
            {confirmClear && <div role="alert" className="rounded-lg border p-4" style={{ borderColor: 'var(--content-error)' }}><p className="font-medium text-primary">Clear the entire regulatory profile?</p><p className="text-sm text-secondary mt-1">Canonical deadline views will return to configuration required.</p><div className="flex flex-wrap gap-3 mt-3"><button type="button" className="btn-primary disabled:cursor-not-allowed disabled:opacity-50" disabled={saving} onClick={() => void persist(clearedRegulatoryProfile(profile))}>{saving ? 'Clearing…' : 'Confirm clear'}</button><button type="button" className="btn-secondary disabled:cursor-not-allowed disabled:opacity-50" disabled={saving} onClick={() => setConfirmClear(false)}>Cancel</button></div></div>}
          </fieldset>
        </form>
      </div>
    </section>
  );
}

export function RegulatoryProfileCard(): ReactElement {
  const { authMethod, user } = useAuth();
  const canEdit = canManageRegulatoryProfile(authMethod, user?.role);

  if (!canEdit) {
    return <section id="regulatory-profile" className="card mb-6" aria-labelledby="regulatory-profile-title"><div className="px-6 py-5"><div className="flex items-center gap-2"><CalendarDays className="w-5 h-5" aria-hidden="true" /><h2 id="regulatory-profile-title" className="text-xl font-semibold text-primary">Regulatory deadline profile</h2></div><p className="text-sm text-tertiary mt-2">Contact an institution administrator to review or change the regulatory framework and canonical deadline used by Aelira.</p></div></section>;
  }

  return <EditableRegulatoryProfileCard />;
}
