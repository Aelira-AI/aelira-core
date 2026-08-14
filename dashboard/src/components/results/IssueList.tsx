import React from 'react';
import { AlertCircle, AlertTriangle, Info, Zap, Search, Eye, LucideIcon } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

type SeverityLevel = 'critical' | 'high' | 'medium' | 'low';

interface SeverityConfig {
  icon: LucideIcon;
  color: string;
  bg: string;
  border: string;
  label: string;
}

interface ColorBlindnessIssue {
  type: string;
  severity: 'critical' | 'serious' | 'moderate' | 'minor';
  description: string;
  contrast: number;
  suggested_fix?: string;
}

interface Issue {
  severity?: string;
  impact?: string;
  criterion?: string;
  wcag_criterion?: string;
  message?: string;
  description?: string;
  rule?: string;
  type?: string;
  title?: string;
  issue_type?: string;
  category?: string;
  text?: string;
  alt_text?: string;
  page_url?: string;
  page_number?: number;
  location?: string;
  element?: string;
  selector?: string;
  xpath?: string;
  screenshot?: string;
  fix?: string;
  suggested_fix?: string;
  fix_suggestion?: string;
  recommendation?: string;
  how_to_fix?: string;
  generated_code_fix?: string;
  ai_generated_fix?: string;
  code_snippet?: string;
  file_path?: string;
  line_number?: number;
  error?: string;
  reason?: string;
  latex?: string;
  equation_id?: number;
  detected_by?: string[];
  color_blindness_issues?: ColorBlindnessIssue[];
}

interface IssueListProps {
  issues: Issue[] | null | undefined;
  scanType?: string;
}

// ============================================================================
// Constants
// ============================================================================

/** Map backend issue `type` field to a human-readable title. */
const ISSUE_TYPE_TITLES: Record<string, string> = {
  heading: 'Heading Structure',
  image: 'Image Accessibility',
  table: 'Table Accessibility',
  list: 'List Structure',
  link: 'Link Accessibility',
  language: 'Language Declaration',
  contrast: 'Color Contrast',
  alt_text: 'Alternative Text',
  navigation: 'Navigation',
  form: 'Form Accessibility',
  structure: 'Document Structure',
  reading_order: 'Reading Order',
  chart: 'Chart Accessibility',
  sheet: 'Sheet Accessibility',
  captions: 'Captions',
  transcript: 'Transcript',
  font_size: 'Font Size',
  title: 'Document Title',
  aria: 'ARIA Attributes',
  keyboard: 'Keyboard Access',
  sheet_name: 'Sheet Name',
  table_header: 'Table Header',
  merge: 'Merged Cells',
  color: 'Color-Only Information',
  // LaTeX scanner - Document Metadata
  missing_title: 'Missing Document Title',
  missing_author: 'Missing Document Author',
  missing_lang: 'Missing Language Declaration',
  // LaTeX scanner - Figures & Images
  missing_alt_text: 'Missing Image Alt Text',
  missing_figure_caption: 'Missing Figure Caption',
  // LaTeX scanner - Tables
  missing_table_caption: 'Missing Table Caption',
  complex_table_no_header: 'Table Without Headers',
  // LaTeX scanner - Equations
  equation_no_label: 'Equation Without Label',
  conversion_failed: 'LaTeX Conversion Failed',
  wcag_noncompliant: 'WCAG Non-Compliant Equation',
  // LaTeX scanner - Color & Links
  color_only_emphasis: 'Color-Only Emphasis',
  low_contrast_potential: 'Potential Low Contrast',
  unlabeled_hyperlink: 'Bare URL Without Description',
  missing_list_structure: 'Manual List Formatting',
  // LaTeX PDF scanner - Math/Equation detection
  latex_equations_inaccessible: 'LaTeX Equations Inaccessible',
  math_content_accessibility: 'Math Content Accessibility',
  raw_latex_code: 'Raw LaTeX Code Detected',
  mathml_recommendation: 'MathML Conversion Recommended',
  // Code scanner
  html: 'HTML Issue',
  css: 'CSS Issue',
  javascript: 'JavaScript Issue',
  // Shadow DOM
  shadow_dom: 'Shadow DOM Component',
  'image-alt': 'Shadow DOM Image',
  'button-name': 'Shadow DOM Button',
  'link-name': 'Shadow DOM Link',
  'form-label': 'Shadow DOM Form Input',
  // XLSX - Conditional Formatting & Pivots
  conditional_format: 'Conditional Formatting',
  pivot_table: 'Pivot Table',
  // Multimedia - Flashing & Diarization
  red_flash: 'Red Flash Detected',
  flashing_content: 'Flashing Content',
  speaker_diarization: 'Speaker Identification',
  // PPTX - Animations & Media
  animation: 'Animation Accessibility',
  animation_flash: 'Animation Flash Risk',
  animation_auto: 'Auto-Start Animation',
  embedded_media: 'Embedded Media',
  missing_captions: 'Missing Captions',
  missing_transcript: 'Missing Transcript',
  // DOCX - SmartArt & Embedded
  smartart: 'SmartArt Diagram',
  embedded_object: 'Embedded Object',
  ole_object: 'OLE Object',
  // PDF - Table Accessibility
  table_accessibility: 'Table Accessibility',
};

/** Fallback descriptions when the backend only sends a type/category without a message. */
const ISSUE_TYPE_DESCRIPTIONS: Record<string, string> = {
  heading: 'Document headings are missing, out of order, or skip levels. Proper heading hierarchy (H1 > H2 > H3) helps screen readers navigate the document structure.',
  image: 'An image in the document lacks descriptive alternative text. Screen readers cannot convey the image content to visually impaired users without alt text.',
  table: 'A table is missing proper header cells or structural markup. Without headers, screen readers cannot associate data cells with their column or row labels.',
  list: 'Content that appears as a list is not using proper list markup (bullets or numbered). Assistive technology cannot convey the list structure without semantic tags.',
  link: 'A hyperlink has generic or missing link text (e.g., "click here"). Descriptive link text helps users understand the destination before clicking.',
  language: 'The document does not declare its primary language. Screen readers need the language attribute to use correct pronunciation rules.',
  contrast: 'Text does not meet the minimum color contrast ratio (4.5:1 for normal text, 3:1 for large text) against its background, making it hard to read.',
  alt_text: 'An image is missing alternative text. All non-decorative images must have descriptive alt text so screen readers can convey the content.',
  navigation: 'Navigation elements are missing or improperly structured. Keyboard-only users and screen readers rely on consistent navigation patterns.',
  form: 'A form field is missing a label or has an improperly associated label. Users relying on assistive technology cannot identify what information to enter.',
  structure: 'The document lacks proper structural markup (headings, regions, landmarks). This makes it difficult for assistive technology to navigate the content.',
  reading_order: 'The visual reading order does not match the underlying document order. Screen readers may present content in a confusing sequence.',
  chart: 'A chart or graph lacks a text description or accessible data table. Users who cannot see the chart need an alternative way to access the data.',
  sheet: 'A spreadsheet has accessibility issues such as missing headers, unclear cell references, or merged cells that confuse screen readers.',
  captions: 'Video or audio content is missing synchronized captions. Deaf or hard-of-hearing users need captions to access the spoken content.',
  transcript: 'Audio or video content lacks a text transcript. Transcripts provide an alternative for users who cannot hear or see the media.',
  font_size: 'Text is too small to read comfortably. Minimum recommended font size is 12pt for body text to ensure readability.',
  title: 'The document is missing a descriptive title. The title is the first thing announced by screen readers and appears in browser tabs.',
  aria: 'ARIA attributes are missing, incorrect, or misused. Improper ARIA can make content less accessible rather than more.',
  keyboard: 'Interactive elements cannot be reached or operated using only the keyboard. All functionality must be available without a mouse.',
  sheet_name: 'A spreadsheet tab has a generic name (e.g., "Sheet1"). Descriptive sheet names help users navigate workbooks with multiple tabs.',
  table_header: 'A data table is missing header row or column markup. Screen readers need headers to announce cell context when navigating tables.',
  merge: 'The spreadsheet contains merged cells which can confuse screen readers and make data relationships unclear.',
  color: 'Information is conveyed using color alone without a text or pattern alternative. Color-blind users may miss the distinction.',
  // LaTeX scanner issues
  missing_title: 'The LaTeX document is missing a \\title{} declaration. Document titles help screen readers identify the document and appear in browser tabs.',
  missing_author: 'The LaTeX document is missing an \\author{} declaration. Author metadata helps with document identification and accessibility.',
  missing_lang: 'The LaTeX document does not specify a language (via babel package or documentclass option). Screen readers need language information for correct pronunciation.',
  missing_alt_text: 'An image (\\includegraphics) is included without alternative text or a figure caption. Visually impaired users cannot understand the image content.',
  missing_figure_caption: 'A figure environment is missing a \\caption{}. Captions provide essential context for figures and are announced by screen readers.',
  missing_table_caption: 'A table environment is missing a \\caption{}. Table captions help users understand the purpose and content of the data.',
  complex_table_no_header: 'A table lacks clear header row separation (\\hline or booktabs commands). Screen readers need headers to navigate table data.',
  equation_no_label: 'A display equation is missing a \\label{} for cross-referencing. Labels enable accessible references like "see Equation 1" in text.',
  color_only_emphasis: 'Text uses \\textcolor{} without additional emphasis (bold, italic). Color-blind users may miss the visual distinction.',
  low_contrast_potential: 'A light color (yellow, lightgray, etc.) is used which may have insufficient contrast when compiled to PDF.',
  unlabeled_hyperlink: 'A bare URL is used with \\url{} instead of descriptive text via \\href{url}{description}. Screen reader users hear the full URL.',
  missing_list_structure: 'Manual list formatting (bullets/numbers as text) is used instead of proper list environments (itemize, enumerate).',
  conversion_failed: 'A LaTeX equation could not be converted to accessible MathML format. The equation will not be readable by screen readers.',
  wcag_noncompliant: 'A LaTeX equation was converted but does not meet WCAG accessibility standards. Additional ARIA labels or descriptions may be needed.',
  // LaTeX PDF scanner - Math/Equation descriptions
  latex_equations_inaccessible: 'This PDF was compiled from LaTeX and contains mathematical equations that may not be accessible. Screen readers cannot read equations rendered as images or untagged content.',
  math_content_accessibility: 'This document contains mathematical content that may not be accessible to screen readers. Consider converting equations to MathML format.',
  raw_latex_code: 'Raw LaTeX code is visible in the document instead of rendered math. This indicates a compilation issue that makes the content completely inaccessible.',
  mathml_recommendation: 'For optimal accessibility, consider converting mathematical equations to MathML format, which allows screen readers to read equations mathematically.',
  html: 'An HTML element has accessibility issues such as missing attributes, incorrect nesting, or missing semantic markup.',
  css: 'CSS styling creates accessibility issues such as hidden content, insufficient contrast, or disabled user scaling.',
  javascript: 'JavaScript creates accessibility barriers such as keyboard traps, focus management issues, or dynamic content that is not announced.',
  // Shadow DOM
  shadow_dom: 'Content inside Shadow DOM may not be accessible to assistive technologies. Ensure proper ARIA attributes on web components.',
  'image-alt': 'Image inside Shadow DOM is missing alternative text. Add alt attribute to describe the image content.',
  'button-name': 'Button inside Shadow DOM has no accessible name. Add aria-label or visible text content.',
  'link-name': 'Link inside Shadow DOM has no accessible name. Add descriptive link text or aria-label.',
  'form-label': 'Form input inside Shadow DOM is missing a label. Associate a label element or use aria-label.',
  // XLSX - Conditional Formatting & Pivots
  conditional_format: 'Conditional formatting uses color alone to convey information. Add text indicators or a legend for color-coded values.',
  pivot_table: 'Pivot tables have complex structure that screen readers struggle with. Consider providing a flat data table alternative.',
  // Multimedia - Flashing & Diarization
  red_flash: 'Saturated red flashing detected. This poses a seizure risk for photosensitive users per WCAG 2.3.1. Remove or reduce red flash intensity.',
  flashing_content: 'Content flashes more than 3 times per second. This may trigger seizures in photosensitive users. Reduce flash frequency below 3Hz.',
  speaker_diarization: 'Multiple speakers detected in audio content. Captions should identify who is speaking for clarity.',
  // PPTX - Animations & Media
  animation: 'Animation may cause issues for users with motion sensitivity or vestibular disorders. Provide option to pause or disable.',
  animation_flash: 'Animation creates rapid flashing that may trigger seizures. Reduce animation speed or remove rapid transitions.',
  animation_auto: 'Animation starts automatically without user control. Add user control to play, pause, or stop animations.',
  embedded_media: 'Embedded media requires captions for deaf users, transcripts for deafblind users, and audio descriptions for blind users.',
  missing_captions: 'Video content is missing synchronized captions. Deaf and hard-of-hearing users need captions to access spoken content.',
  missing_transcript: 'Audio content is missing a text transcript. Provide a complete transcript for users who cannot hear the audio.',
  // DOCX - SmartArt & Embedded
  smartart: 'SmartArt diagram requires alternative text describing its meaning and relationships. Add a text description in alt text field.',
  embedded_object: 'Embedded object needs alternative text or accessible alternative. Screen readers cannot access embedded object content.',
  ole_object: 'OLE embedded object may not be accessible. Provide a text description or accessible version of the embedded content.',
  // PDF - Table Accessibility
  table_accessibility: 'This table has complex structure that may be difficult for screen readers to navigate. Add proper header tags with scope attributes.',
};

const SEVERITY_CONFIG: Record<SeverityLevel, SeverityConfig> = {
  critical: {
    icon: AlertCircle,
    color: 'text-[var(--feature-danger-content)]',
    bg: 'bg-[var(--feature-danger-surface)]',
    border: 'border-[var(--feature-danger-content)]',
    label: 'Critical',
  },
  high: {
    icon: AlertTriangle,
    color: 'text-[var(--feature-warning-content)]',
    bg: 'bg-[var(--feature-warning-surface)]',
    border: 'border-[var(--feature-warning-content)]',
    label: 'High',
  },
  medium: {
    icon: AlertTriangle,
    color: 'text-[var(--feature-info-content)]',
    bg: 'bg-[var(--feature-info-surface)]',
    border: 'border-[var(--feature-info-content)]',
    label: 'Medium',
  },
  low: {
    icon: Info,
    color: 'text-secondary',
    bg: 'bg-surface-tertiary',
    border: 'border-[var(--border-primary)]',
    label: 'Low',
  },
};

// ============================================================================
// Component
// ============================================================================

export function IssueList({
  issues,
  scanType = 'document',
}: IssueListProps): React.ReactElement {
  if (!issues || issues.length === 0) {
    const contentType = scanType === 'website' ? 'website' : 'document';
    return (
      <div className="card text-center py-8">
        <p className="text-secondary">No issues found! Your {contentType} is fully accessible.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {issues.map((issue, index) => {
        // Map backend severity/impact values to display levels
        // PDF/DOCX/XLSX: critical/high/medium/low
        // Code scanner: critical/serious/moderate/minor
        // LaTeX: error/high/medium
        const rawSeverity = issue.severity || issue.impact;
        let severity: SeverityLevel = 'low';
        if (rawSeverity === 'critical' || rawSeverity === 'error') severity = 'critical';
        else if (rawSeverity === 'serious' || rawSeverity === 'high') severity = 'high';
        else if (rawSeverity === 'moderate' || rawSeverity === 'medium') severity = 'medium';
        else if (rawSeverity === 'minor' || rawSeverity === 'low') severity = 'low';

        const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.low;
        const Icon = config.icon;

        // Build human-readable title from available fields
        // Priority: title > type-based label > category-based label > rule > fallback
        const issueTitle =
          issue.title ||
          (issue.type ? ISSUE_TYPE_TITLES[issue.type] || issue.type : null) ||
          (issue.category ? ISSUE_TYPE_TITLES[issue.category] || issue.category : null) ||
          issue.rule ||
          'Accessibility Issue';

        // WCAG criterion badge (e.g., "1.3.1")
        const wcagCriterion = issue.criterion || issue.wcag_criterion;

        // Description text - cascade through all possible fields
        // LaTeX uses error/reason, video uses message, code uses description
        // Fall back to rich type-based descriptions when backend sends no message
        const issueTypeKey = issue.type || issue.category || '';
        const descriptionText = issue.alt_text
          ? (issue.message || issue.description || 'Image missing alternative text')
          : (issue.message || issue.description || issue.error || issue.reason
            || ISSUE_TYPE_DESCRIPTIONS[issueTypeKey] || issue.impact || issue.issue_type || '');

        // Fix suggestion - backend uses several field names depending on scanner
        // DOCX/XLSX: suggested_fix, Code: fix_suggestion, Video: recommendation, Website: fix
        const fixText = issue.fix || issue.suggested_fix || issue.fix_suggestion
          || issue.recommendation || issue.how_to_fix;

        return (
          <div key={index} className={`card border ${config.border} ${config.bg}`}>
            <div className="flex items-start space-x-4">
              <Icon className={`w-5 h-5 ${config.color} mt-1`} />
              <div className="flex-1">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold text-primary text-base">
                      {issueTitle}
                    </h3>
                    {wcagCriterion && (
                      <span className="text-xs font-mono font-medium px-2 py-0.5 rounded bg-[var(--surface-tertiary)] text-secondary border border-[var(--border-primary)]">
                        WCAG {wcagCriterion}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {/* Engine Attribution Badge (Pa11y multi-engine integration) */}
                    {issue.detected_by && issue.detected_by.length > 0 && (
                      <div className="flex items-center gap-1">
                        {issue.detected_by.includes('axe-core') && (
                          <span
                            className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-[var(--feature-info-surface)] text-[var(--feature-info-content)] border border-[var(--border-primary)]"
                            title="Detected by axe-core"
                          >
                            <Zap className="w-3 h-3" />
                            axe
                          </span>
                        )}
                        {issue.detected_by.includes('pa11y') && (
                          <span
                            className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-[var(--feature-primary-surface)] text-[var(--feature-primary-content)] border border-[var(--border-accent)]"
                            title="Detected by Pa11y"
                          >
                            <Search className="w-3 h-3" />
                            Pa11y
                          </span>
                        )}
                        {issue.detected_by.includes('ai-vision') && (
                          <span
                            className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded bg-[var(--feature-primary-surface)] text-[var(--feature-primary-content)] border border-[var(--border-accent)]"
                            title="Detected by AI Vision"
                          >
                            <Eye className="w-3 h-3" />
                            AI
                          </span>
                        )}
                      </div>
                    )}
                    <span className={`text-xs font-medium px-2 py-1 rounded ${config.bg} ${config.color}`}>
                      {config.label}
                    </span>
                  </div>
                </div>
                {descriptionText && (
                  <p className="text-sm text-primary mb-2">
                    {descriptionText}
                  </p>
                )}

                {/* Page URL (for website scans) */}
                {issue.page_url && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">Page:</p>
                    <p className="text-xs text-primary break-all">{issue.page_url}</p>
                  </div>
                )}

                {/* PDF Page Number + Location */}
                {issue.page_number && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">Location:</p>
                    <p className="text-xs text-primary">
                      {issue.location || `Page ${issue.page_number}`}
                    </p>
                  </div>
                )}

                {/* Element info */}
                {issue.element && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">Element:</p>
                    <code className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono">
                      {issue.element}
                    </code>
                  </div>
                )}

                {/* File path + line number (for code scans) */}
                {issue.file_path && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">File:</p>
                    <code className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono">
                      {issue.file_path}{issue.line_number ? `:${issue.line_number}` : ''}
                    </code>
                  </div>
                )}

                {/* Code snippet (for code scans) */}
                {issue.code_snippet && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">Code:</p>
                    <pre className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono overflow-x-auto whitespace-pre-wrap">
                      {issue.code_snippet}
                    </pre>
                  </div>
                )}

                {/* LaTeX source (for LaTeX scans) */}
                {issue.latex && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">LaTeX:</p>
                    <code className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono">
                      {issue.latex}
                    </code>
                  </div>
                )}

                {/* CSS Selector (for website scans) */}
                {issue.selector && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">CSS Selector:</p>
                    <code className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono">
                      {issue.selector}
                    </code>
                  </div>
                )}

                {/* XPath (for website scans) */}
                {issue.xpath && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-secondary">XPath:</p>
                    <code className="text-xs bg-surface-tertiary text-primary px-2 py-1 rounded block break-all font-mono">
                      {issue.xpath}
                    </code>
                  </div>
                )}

                {/* Screenshot */}
                {issue.screenshot && (
                  <div className="mt-3">
                    <p className="text-xs font-medium text-secondary mb-1">Element Screenshot:</p>
                    <img
                      src={`data:image/png;base64,${issue.screenshot}`}
                      alt="Element screenshot"
                      className="max-w-full h-auto border border-[var(--border-primary)] rounded max-h-[300px]"
                    />
                  </div>
                )}

                {/* Generic location (fallback if no page_url) */}
                {issue.location && !issue.page_url && (
                  <p className="text-xs text-secondary mt-2">Location: {issue.location}</p>
                )}

                {/* How to Fix */}
                {fixText && (
                  <div className="mt-3 p-3 bg-surface-tertiary rounded border border-[var(--border-primary)]">
                    <p className="text-xs font-medium text-secondary mb-1">How to Fix:</p>
                    <p className="text-xs text-secondary">{fixText}</p>
                  </div>
                )}

                {/* AI-Generated Alt Text (for images) */}
                {issue.alt_text && (
                  <div className="mt-3 p-3 bg-[var(--feature-primary-surface)] rounded border border-[var(--border-accent)]">
                    <p className="text-xs font-medium text-[var(--feature-primary-content)] mb-2">
                      AI-Generated Alt Text:
                    </p>
                    <p className="text-sm text-[var(--feature-primary-content)] bg-[var(--surface-primary)] p-3 rounded border border-[var(--border-accent)]">
                      "{issue.alt_text}"
                    </p>
                  </div>
                )}

                {/* AI-Generated Code Fix (for websites and code scans) */}
                {(issue.generated_code_fix || issue.ai_generated_fix) && !issue.alt_text && (
                  <div className="mt-3 p-3 bg-[var(--feature-primary-surface)] rounded border border-[var(--border-accent)]">
                    <p className="text-xs font-medium text-[var(--feature-primary-content)] mb-2">
                      AI-Generated Code Fix:
                    </p>
                    <pre className="text-xs text-[var(--feature-primary-content)] whitespace-pre-wrap break-all font-mono bg-[var(--surface-primary)] p-2 rounded border border-[var(--border-accent)] overflow-x-auto">
                      {issue.generated_code_fix || issue.ai_generated_fix}
                    </pre>
                  </div>
                )}

                {/* Color Blindness Issues */}
                {issue.color_blindness_issues && issue.color_blindness_issues.length > 0 && (
                  <div className="mt-3 p-3 bg-[var(--feature-primary-surface)] rounded border border-[var(--border-accent)]">
                    <p className="text-xs font-medium text-[var(--feature-primary-content)] mb-3">
                      Color Blindness Issues (Affects ~8% of males)
                    </p>
                    <div className="space-y-2">
                      {issue.color_blindness_issues.map((cvdIssue, cvdIndex) => (
                        <div
                          key={cvdIndex}
                          className="bg-[var(--surface-primary)] p-3 rounded border border-[var(--border-accent)]"
                        >
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-xs font-semibold text-[var(--feature-primary-content)] uppercase">
                              {cvdIssue.type.replace(/_/g, ' ')}
                            </span>
                            <span
                              className={`text-xs font-medium px-2 py-1 rounded ${
                                cvdIssue.severity === 'critical'
                                  ? 'bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)]'
                                  : cvdIssue.severity === 'serious'
                                    ? 'bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]'
                                    : 'bg-[var(--feature-info-surface)] text-[var(--feature-info-content)]'
                              }`}
                            >
                              {cvdIssue.severity}
                            </span>
                          </div>
                          <p className="text-xs text-[var(--feature-primary-content)] mb-2">
                            {cvdIssue.description}
                          </p>
                          <div className="text-xs text-[var(--content-secondary)] mb-1">
                            <strong>Simulated Contrast:</strong> {cvdIssue.contrast}:1
                          </div>
                          {cvdIssue.suggested_fix && (
                            <div className="mt-2 p-2 bg-[var(--surface-secondary)] rounded border border-[var(--border-accent)]">
                              <p className="text-xs font-medium text-[var(--feature-primary-content)] mb-1">
                                Suggested Fix:
                              </p>
                              <p className="text-xs text-[var(--feature-primary-content)]">
                                {cvdIssue.suggested_fix}
                              </p>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
