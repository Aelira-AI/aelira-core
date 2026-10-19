/**
 * Name a downloaded remediation output after what it actually contains.
 * Generated captions are companion files, so they must not inherit the
 * source media's name or extension.
 */
export function remediationDownloadName(fileName: string | undefined, mimeType: string): string {
  if (mimeType.split(';')[0].trim().toLowerCase() === 'text/vtt') {
    const stem = fileName ? fileName.replace(/\.[^./\\]+$/, '') : '';
    return stem ? `${stem}-captions.vtt` : 'captions.vtt';
  }
  return `remediated-${fileName || 'document'}`;
}
