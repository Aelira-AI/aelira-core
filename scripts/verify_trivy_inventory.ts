/** Fail closed on an incomplete, mismatched or non-clean image inventory. */
import { readFile, stat } from 'node:fs/promises';
import { parseArgs } from 'node:util';

type RecordValue = Record<string, unknown>;
const isRecord = (value: unknown): value is RecordValue =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const nonempty = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;
const severities = new Set(['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']);

export function verifyInventory(
  value: unknown,
  image: string,
  architecture: 'amd64' | 'arm64',
): string[] {
  if (!isRecord(value)) return ['Inventory must be a JSON object'];
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
    return [...errors, 'Inventory lacks scanner results'];
  }
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
      if (finding.Severity === 'HIGH') high++;
      if (finding.Severity === 'CRITICAL') critical++;
    }
  }
  if (!osScanned) errors.push('Inventory lacks the identified OS package scan');
  if (high || critical) errors.push(`Unresolved image findings: ${high} HIGH, ${critical} CRITICAL`);
  return [...new Set(errors)];
}

if (import.meta.main) {
  try {
    const { values, positionals } = parseArgs({
      options: {
        file: { type: 'string' }, image: { type: 'string' }, architecture: { type: 'string' },
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
    const errors = verifyInventory(inventory, values.image, values.architecture);
    if (errors.length) {
      for (const error of errors) console.error(error);
      process.exitCode = 1;
    } else {
      console.log('Image report contains no HIGH or CRITICAL vulnerability findings');
    }
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Inventory verification failed');
    process.exitCode = 1;
  }
}
