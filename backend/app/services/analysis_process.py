"""Bounded analysis process; disk archiving is queued independently by the caller."""
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace

_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)
_pool = None

INPUT_KEYS = ('captured_profiles', 'captured_profile_positions', 'profiling_distance_mm',
              'profiles_count', 'distance_mm', 'devices', 'quality_issues', 'measurement_valid',
              'measurement_encoder_error', 'measurement_encoder_error_message',
              'raw_frames', 'raw_frames_received', 'raw_frames_dropped',
              'capture_calibrations', 'capture_motion', 'relative_fragments', 'analysis_filter_settings')
OUTPUT_KEYS = ('analysis_metrics', 'analysis_points', 'analysis_duration_ms',
               'analysis_timestamp_ms', 'analysis_app', 'quality_issues', 'measurement_valid',
               'archive_last_error', 'archive_last_duration_ms', 'archive_last_points_count',
               'archive_last_ts')


def _analyze(snapshot, settings):
    from app.api.endpoints import acquisition

    acquisition.device_manager.analysis_settings = settings
    acquisition.device_manager.output_settings = {'enabled': False}

    archive_payload = {}

    def save(session, payload):
        archive_payload.update(payload)
        archive_payload['original_points'] = session.get('analysis_input_points', [])

    acquisition._save_measurement_async = save
    snapshot['captured_full_cache_dirty'] = True
    acquisition._run_analysis_local(snapshot, SimpleNamespace(state=SimpleNamespace(tcp_notifier=None)))
    # Compact IPC payload instead of pickling hundreds of thousands of Python floats.
    import numpy as np
    archive_payload['original_points'] = np.asarray(archive_payload.get('original_points') or [], dtype=np.float32)
    return {**{key: snapshot.get(key) for key in OUTPUT_KEYS}, '_archive_payload': archive_payload}


def analyze_capture(session, settings):
    global _pool
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Analysis queue full')
    try:
        snapshot = {key: session.get(key) for key in INPUT_KEYS if key not in ('raw_frames', 'relative_fragments')}
        snapshot['relative_fragments'] = [{'profiles_count': f['profiles_count']}
                                          for f in session.get('relative_fragments') or []]
        with _lock:
            if _pool is None:
                _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
            submitted_pool = _pool
            try:
                future = submitted_pool.submit(_analyze, snapshot, settings)
            except BrokenProcessPool:
                submitted_pool.shutdown(wait=False, cancel_futures=True)
                _pool = None
                raise
        # Release capacity only when the actual work finishes, including on timeout.
        future.add_done_callback(lambda _: _slots.release())
    except BaseException:
        _slots.release()
        raise
    try:
        return future.result(timeout=120)
    except BrokenProcessPool:
        with _lock:
            if _pool is submitted_pool:
                _pool.shutdown(wait=False, cancel_futures=True)
                _pool = None
        raise
