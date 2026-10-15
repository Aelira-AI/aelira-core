/** Fail closed on an incomplete, mismatched or non-clean image inventory. */
import { readFile, stat } from 'node:fs/promises';
import { parseArgs } from 'node:util';

type RecordValue = Record<string, unknown>;
const isRecord = (value: unknown): value is RecordValue =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const nonempty = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;
const severities = new Set(['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);
const isoDate = /^\d{4}-\d{2}-\d{2}$/;
const maxDispositionDays = 120;

/**
 * A reviewed HIGH distribution-package finding that has no vendor fix. It is
 * still reported; it only stops counting as unresolved while every field
 * matches the scanned row exactly and the review has not expired.
 */
export type Disposition = {
  id: string;
  osFamily: string;
  package: string;
  installedVersion: string;
  reason: string;
  reviewed: string;
  expires: string;
  disclosure: string;
};

const validDate = (value: unknown): value is string =>
  typeof value === 'string' && isoDate.test(value) &&
  new Date(`${value}T00:00:00Z`).toISOString().startsWith(value);
const days = (from: string, to: string) =>
  (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000;

export function verifyDispositions(value: unknown, today: string): { dispositions: Disposition[]; errors: string[] } {
  if (!isRecord(value) || value.schemaVersion !== 1 || !Array.isArray(value.dispositions)) {
    return { dispositions: [], errors: ['Dispositions must be a schemaVersion 1 object with a dispositions array'] };
  }
  const errors: string[] = [];
  const dispositions: Disposition[] = [];
  const seen = new Set<string>();
  for (const entry of value.dispositions) {
    if (!isRecord(entry) || !nonempty(entry.id) || !/^CVE-\d{4}-\d{4,}$/.test(entry.id) ||
        !nonempty(entry.osFamily) || !nonempty(entry.package) || !nonempty(entry.installedVersion) ||
        !nonempty(entry.reason) || !nonempty(entry.disclosure) ||
        !validDate(entry.reviewed) || !validDate(entry.expires)) {
      errors.push('Dispositions contain a malformed entry');
      continue;
    }
    const key = `${entry.id} ${entry.osFamily} ${entry.package} ${entry.installedVersion}`;
    if (seen.has(key)) errors.push(`Duplicate disposition: ${key}`);
    seen.add(key);
    if (days(entry.reviewed, today) < 0) errors.push(`Disposition reviewed in the future: ${entry.id}`);
    if (days(today, entry.expires) <= 0) errors.push(`Disposition expired: ${entry.id}`);
    if (days(entry.reviewed, entry.expires) > maxDispositionDays) {
      errors.push(`Disposition review window exceeds ${maxDispositionDays} days: ${entry.id}`);
    }
    dispositions.push(entry as Disposition);
  }
  return { dispositions, errors };
}

export type InventoryReport = { errors: string[]; accepted: Disposition[] };

export function verifyInventory(
  value: unknown,
  image: string,
  architecture: 'amd64' | 'arm64',
  dispositions: Disposition[] = [],
): string[] {
  return inspectInventory(value, image, architecture, dispositions).errors;
}

export function inspectInventory(
  value: unknown,
  image: string,
  architecture: 'amd64' | 'arm64',
  dispositions: Disposition[] = [],
): InventoryReport {
  const report = (errors: string[], accepted: Disposition[] = []) =>
    ({ errors: [...new Set(errors)], accepted: [...new Set(accepted)] });
  if (!isRecord(value)) return report(['Inventory must be a JSON object']);
  const errors: string[] = [];
  if (value.SchemaVersion !== 2) errors.push('Unsupported inventory schema');
  const scanner = isRecord(value.Trivy) ? value.Trivy : {};
  if (scanner.Version !== '0.74.0') errors.push('Inventory does not identify the pinned scanner version');
  if (value.ArtifactType !== 'container_image' || value.ArtifactName !== image) {
    errors.push('Inventory does not identify the expected container image');
  }
  const metadata = isRecord(value.Metadata) ? value.Metadata : {};
  const config = isRecord(metadata.ImageConfig) ? metadata.ImageConfig : {};
  if (config.architecture !== architecture || config.os !== 'linux') {
    errors.push('Inventory does not identify the expected native Linux platform');
  }
  if (typeof metadata.ImageID !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(metadata.ImageID)) {
    errors.push('Inventory lacks a valid image configuration identity');
  }
  const os = isRecord(metadata.OS) ? metadata.OS : {};
  if (!nonempty(os.Family) || !nonempty(os.Name)) errors.push('Inventory lacks OS identification');
  if (!Array.isArray(value.Results) || value.Results.length === 0) {
    return report([...errors, 'Inventory lacks scanner results']);
  }
  const accepted: Disposition[] = [];
  let osScanned = false;
  let high = 0;
  let critical = 0;
  for (const result of value.Results) {
    if (!isRecord(result) || !nonempty(result.Target) || !nonempty(result.Class)) {
      errors.push('Inventory contains a malformed scanner result');
      continue;
    }
    if (result.Class === 'os-pkgs' || result.Class === 'lang-pkgs') {
      if (!nonempty(result.Type)) errors.push('Inventory contains a package result without its type');
    } else if (result.Class === 'secret') {
      // Trivy omits Type for secret results. Any vulnerability rows still undergo
      // the same validation below; a result class never exempts findings.
      if (result.Type !== undefined && typeof result.Type !== 'string') {
        errors.push('Inventory contains a malformed secret result type');
      }
    } else {
      errors.push('Inventory contains an unsupported scanner result class');
    }
    if (result.Class === 'os-pkgs' && result.Type === os.Family) osScanned = true;
    if (result.Vulnerabilities === undefined || result.Vulnerabilities === null) continue;
    if (!Array.isArray(result.Vulnerabilities)) {
      errors.push('Inventory contains a malformed vulnerability collection');
      continue;
    }
    for (const finding of result.Vulnerabilities) {
      if (!isRecord(finding) || !nonempty(finding.VulnerabilityID) ||
          !nonempty(finding.PkgName) || !nonempty(finding.InstalledVersion) ||
          typeof finding.Severity !== 'string' || !severities.has(finding.Severity)) {
        errors.push('Inventory contains a malformed vulnerability');
        continue;
      }
      if (finding.Severity === 'HIGH') {
        // Only distribution packages without any vendor fix can carry a
        // disposition. CRITICAL rows, language packages and fixable rows never can.
        const unfixed = finding.FixedVersion === undefined || finding.FixedVersion === null ||
          finding.FixedVersion === '';
        const match = result.Class === 'os-pkgs' && result.Type === os.Family && unfixed
          ? dispositions.find((entry) => entry.id === finding.VulnerabilityID &&
            entry.osFamily === os.Family && entry.package === finding.PkgName &&
            entry.installedVersion === finding.InstalledVersion)
          : undefined;
        if (match) accepted.push(match);
        else high++;
      }
      if (finding.Severity === 'CRITICAL') critical++;
    }
  }
  if (!osScanned) errors.push('Inventory lacks the identified OS package scan');
  if (high || critical) errors.push(`Unresolved image findings: ${high} HIGH, ${critical} CRITICAL`);
  return report(errors, accepted);
}

if (import.meta.main) {
  try {
    const { values, positionals } = parseArgs({
      options: {
        file: { type: 'string' }, image: { type: 'string' }, architecture: { type: 'string' },
        dispositions: { type: 'string' }, today: { type: 'string' },
      },
      allowPositionals: false,
    });
    if (!values.file || !values.image || positionals.length ||
        (values.architecture !== 'amd64' && values.architecture !== 'arm64')) {
      throw new Error('Use --file, --image and --architecture amd64|arm64');
    }
    const info = await stat(values.file);
    if (!info.isFile() || info.size > 64 * 1024 * 1024) {
      throw new Error('Inventory must be a regular file of at most 64 MiB');
    }
    let inventory: unknown;
    try {
      inventory = JSON.parse(await readFile(values.file, 'utf8'));
    } catch {
      throw new Error('Inventory is not readable valid JSON');
    }
    const today = values.today ?? new Date().toISOString().slice(0, 10);
    if (!validDate(today)) throw new Error('Use --today YYYY-MM-DD');
    let dispositions: Disposition[] = [];
    const errors: string[] = [];
    if (values.dispositions) {
      let raw: unknown;
      try {
        raw = JSON.parse(await readFile(values.dispositions, 'utf8'));
      } catch {
        throw new Error('Dispositions are not readable valid JSON');
      }
      const checked = verifyDispositions(raw, today);
      dispositions = checked.dispositions;
      errors.push(...checked.errors);
    }
    const result = inspectInventory(inventory, values.image, values.architecture, dispositions);
    errors.push(...result.errors);
    for (const entry of result.accepted) {
      let disclosure = '';
      try {
        disclosure = await readFile(entry.disclosure, 'utf8');
      } catch {
        // Reported below as an undisclosed finding.
      }
      if (!disclosure.includes(entry.id)) errors.push(`Disclosure ${entry.disclosure} does not name ${entry.id}`);
    }
    for (const entry of result.accepted) {
      console.log(`Reported unfixed HIGH ${entry.id} in ${entry.package} ${entry.installedVersion}: ` +
        `disclosed in ${entry.disclosure}, review expires ${entry.expires}`);
    }
    if (errors.length) {
      for (const error of [...new Set(errors)]) console.error(error);
      process.exitCode = 1;
    } else if (result.accepted.length) {
      console.log('Image report contains no unresolved HIGH or CRITICAL findings; the unfixed HIGH findings above are disclosed');
    } else {
      console.log('Image report contains no HIGH or CRITICAL vulnerability findings');
    }
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Inventory verification failed');
    process.exitCode = 1;
  }
}
