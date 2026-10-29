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

/** Storage names the API gives a working file; they say nothing about what was uploaded. */
const INTERNAL_ARTIFACT_STEM = /^(source|review-edit)(_remediated)?$/i;

/**
 * The name to show and save for a working file. The API names it after its own
 * storage ("source_remediated.docx"); name it after the uploaded file instead,
 * as the remediation page does. A name that already reflects a real file is kept.
 */
export function workingFileName(uploadedName: string | undefined, artifactName: string): string {
  const dot = artifactName.lastIndexOf('.');
  const stem = dot > 0 ? artifactName.slice(0, dot) : artifactName;
  const extension = dot > 0 ? artifactName.slice(dot) : '';
  if (!INTERNAL_ARTIFACT_STEM.test(stem)) return artifactName;
  const uploadedStem = (uploadedName ?? '').replace(/\.[^./\\]+$/, '');
  if (extension.toLowerCase() === '.vtt') return uploadedStem ? `${uploadedStem}-captions.vtt` : 'captions.vtt';
  return `remediated-${uploadedStem || 'document'}${extension}`;
}
