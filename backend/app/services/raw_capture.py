"""Retain complete scanner frames independently of encoder position validity."""
import numpy as np

MAX_RAW_BYTES = 128 * 1024 * 1024


def retain(session, device_id, frames):
    records = session.setdefault('raw_frames', [])
    for frame in frames:
        stamp = frame.get('received_monotonic', 0)
        if stamp < session['capture_started_monotonic']:
            continue
        end = session.get('capture_stopped_monotonic')
        if end is not None and stamp > end:
            continue
        session['raw_frames_received'] = session.get('raw_frames_received', 0) + 1
        points = np.asarray(frame['points'])
        used = session.get('raw_capture_bytes', 0)
        if used + points.nbytes > MAX_RAW_BYTES:
            session.setdefault('quality_issues', set()).add('raw_capture_overflow')
            session['raw_frames_dropped'] = session.get('raw_frames_dropped', 0) + 1
            continue
        records.append(dict(device_id=device_id, frame_number=frame.get('frame'),
                            device_frame_number=frame.get('device_frame'),
                            timestamp=frame.get('timestamp'),
                            received_monotonic=stamp, received_offset_s=stamp-session['capture_started_monotonic'],
                            position_mm=None, position_status='unassigned', points=points.copy()))
        session['raw_capture_bytes'] = used + points.nbytes


def archive_fields(session):
    return dict(raw_frames=[{**record, 'points': record['points'].tolist()}
                            for record in session.get('raw_frames', [])],
                raw_frames_received=session.get('raw_frames_received', 0),
                raw_frames_dropped=session.get('raw_frames_dropped', 0),
                raw_capture_calibrations=session.get('capture_calibrations', {}),
                raw_capture_motion=session.get('capture_motion', {}),
                raw_coordinate_system='scanner_local; position_mm unknown; not a reconstructed cloud')
