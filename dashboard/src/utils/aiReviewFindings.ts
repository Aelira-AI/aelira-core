export interface AIReviewMarker {
  issue_type?: string;
  assessment_type?: string;
  review_only?: boolean;
  scoring_included?: boolean;
}

export function isUnscoredAltReview(issue: AIReviewMarker): boolean {
  return issue.issue_type === 'ai_alt_quality_review'
    && issue.assessment_type === 'ai_alt_quality_review'
    && issue.review_only === true
    && issue.scoring_included === false;
}
