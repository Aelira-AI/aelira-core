import { BookOpen, Settings, Upload, X } from 'lucide-react';

interface DashboardWelcomeProps {
  name?: string;
  configurationRequired: boolean;
  canConfigure: boolean;
  hasIntegrations: boolean;
  onDismiss: () => void;
  onUpload: () => void;
  onConfigure: () => void;
  onGuide: () => void;
}

export function DashboardWelcome({ name, configurationRequired, canConfigure, hasIntegrations,
  onDismiss, onUpload, onConfigure, onGuide }: DashboardWelcomeProps) {
  const brandName = import.meta.env.VITE_BRAND_NAME || 'Aelira';
  const configureTitle = configurationRequired && canConfigure ? 'Configure your institution'
    : hasIntegrations ? 'Connect your LMS' : 'Set your preferences';
  const configureHint = configurationRequired && canConfigure ? 'Verify the regulatory profile'
    : hasIntegrations ? 'Check available integrations' : 'Review your account settings';

  return (
    <section className="workspace-welcome" aria-labelledby="welcome-title">
      <div className="flex items-start justify-between gap-4 mb-5">
        <div>
          <p className="text-xs font-mono uppercase tracking-wider text-[var(--content-accent)] mb-2">Your first steps</p>
          <h2 id="welcome-title" className="text-xl font-bold text-[var(--content-primary)]">
            Welcome to {brandName}{name ? `, ${name.split(' ')[0]}` : ''}!
          </h2>
          <p className="text-sm text-[var(--content-secondary)] mt-2 max-w-2xl">
            {configurationRequired ? 'Finish your institution setup, then scan a document and review the findings.'
              : 'Start with a document, check the findings and review supported changes before publishing.'}
          </p>
        </div>
        <button type="button" onClick={onDismiss} className="p-2 rounded-full text-[var(--content-secondary)] hover:bg-[var(--surface-primary)]" aria-label="Dismiss welcome banner">
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <button type="button" className="workspace-step" onClick={onUpload}>
          <span className="workspace-step-icon"><Upload size={20} aria-hidden="true" /></span>
          <span><strong>Upload a document</strong><small>PDF, PowerPoint, Word or Excel</small></span>
        </button>
        <button type="button" className="workspace-step" onClick={onConfigure}>
          <span className="workspace-step-icon"><Settings size={20} aria-hidden="true" /></span>
          <span><strong>{configureTitle}</strong><small>{configureHint}</small></span>
        </button>
        <a className="workspace-step" href="https://aelira.ai/docs" target="_blank" rel="noopener noreferrer" onClick={onGuide}>
          <span className="workspace-step-icon"><BookOpen size={20} aria-hidden="true" /></span>
          <span><strong>Read the guide</strong><small>Document workflow and review · opens in a new tab</small></span>
        </a>
      </div>
      <p className="text-xs text-[var(--content-secondary)] mt-4">
        Automated checks help prioritise work. A person verifies the saved output and resolves remaining findings.
      </p>
    </section>
  );
}
