export type DeadlineApplicability =
  | 'dated_deadline'
  | 'ongoing_no_date'
  | 'not_applicable'
  | 'configuration_required';

export interface DeadlineInfo {
  applicability: DeadlineApplicability;
  has_deadline: boolean;
  deadline_date: string | null;
  deadline_label: string | null;
  days_remaining: number | null;
  framework_code: string;
  framework_name: string;
  standard: string;
  message: string;
  urgency: 'none' | 'low' | 'medium' | 'high' | 'critical';
  is_past_deadline: boolean;
}

export function hasDatedDeadline(
  deadline: DeadlineInfo | null | undefined,
): deadline is DeadlineInfo & {
  deadline_date: string;
  deadline_label: string;
  days_remaining: number;
} {
  return Boolean(
    deadline?.has_deadline
      && deadline.applicability === 'dated_deadline'
      && !deadline.is_past_deadline
      && deadline.deadline_date
      && deadline.deadline_label
      && deadline.days_remaining !== null,
  );
}
