export interface MeasurementQuality {
  recording?: boolean;
  analysis_pending?: boolean;
  measurement_valid?: boolean | null;
  quality_status?: 'valid' | 'invalid' | 'unknown';
  quality_issues?: string[] | null;
  encoder_error?: string | null;
  encoder_error_message?: string | null;
}

export function qualityStatus(quality?: MeasurementQuality | null) {
  if (quality?.quality_issues?.length || quality?.encoder_error || quality?.encoder_error_message) return 'invalid';
  if (quality?.recording || quality?.analysis_pending) return 'pending';
  if (quality?.quality_status === 'invalid') return 'invalid';
  if (quality?.quality_status === 'unknown') return 'unknown';
  if (quality?.measurement_valid === true) return 'valid';
  if (quality?.measurement_valid === false) return 'invalid';
  return 'unknown';
}
