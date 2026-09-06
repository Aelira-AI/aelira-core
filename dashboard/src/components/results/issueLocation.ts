interface IssueLocationFields {
  location?: string;
  page_url?: string;
  page_number?: number;
}

export function shouldRenderGenericLocation(issue: IssueLocationFields): boolean {
  return Boolean(issue.location && !issue.page_url && !issue.page_number);
}
