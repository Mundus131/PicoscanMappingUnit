import { qualityStatus, type MeasurementQuality } from '@/lib/measurementQuality';

export default function MeasurementQualityNotice({ quality, label = 'Measurement quality' }: {
  quality?: MeasurementQuality | null;
  label?: string;
}) {
  const status = qualityStatus(quality);
  const colors = status === 'valid'
    ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
    : status === 'invalid'
      ? 'border-rose-300 bg-rose-50 text-rose-900'
      : 'border-amber-300 bg-amber-50 text-amber-900';
  return (
    <div role="status" className={`rounded-md border p-3 text-sm ${colors}`}>
      <strong>{label}: {status.toUpperCase()}</strong>
      {status === 'pending' && <div>Capture or analysis in progress; measurement is not yet validated.</div>}
      {status !== 'valid' && status !== 'pending' && <div>Values are diagnostic only; do not treat as a validated measurement.</div>}
      {!!quality?.quality_issues?.length && <div>Issues: {quality.quality_issues.join(', ')}</div>}
      {quality?.encoder_error && <div>Encoder: {quality.encoder_error}</div>}
      {quality?.encoder_error_message && <div>{quality.encoder_error_message}</div>}
    </div>
  );
}
