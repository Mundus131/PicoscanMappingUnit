"""Bounded single-process analysis and archive writing, off the API's GIL."""
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from types import SimpleNamespace

_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)
_pool = None

INPUT_KEYS = ('captured_profiles', 'captured_profile_positions', 'profiling_distance_mm',
              'profiles_count', 'distance_mm', 'devices', 'quality_issues', 'measurement_valid',
              'measurement_encoder_error', 'measurement_encoder_error_message')
OUTPUT_KEYS = ('analysis_metrics', 'analysis_points', 'analysis_duration_ms',
               'analysis_timestamp_ms', 'analysis_app', 'quality_issues', 'measurement_valid',
               'archive_last_error', 'archive_last_duration_ms', 'archive_last_points_count',
               'archive_last_ts')


def _analyze(snapshot, settings):
    from app.api.endpoints import acquisition
    from app.analysis.history_store import save_measurement

    acquisition.device_manager.analysis_settings = settings
    acquisition.device_manager.output_settings = {'enabled': False}

    def save(session, payload):
        started = time.monotonic()
        try:
            save_measurement(payload)
            session['archive_last_error'] = None
            session['archive_last_points_count'] = len(payload.get('original_points') or [])
            session['archive_last_ts'] = int(time.time() * 1000)
        except Exception:
            session['archive_last_error'] = 'Archive write failed'
        session['archive_last_duration_ms'] = int((time.monotonic() - started) * 1000)

    acquisition._save_measurement_async = save
    snapshot['captured_full_cache_dirty'] = True
    acquisition._run_analysis_local(snapshot, SimpleNamespace(state=SimpleNamespace(tcp_notifier=None)))
    return {key: snapshot.get(key) for key in OUTPUT_KEYS}


def analyze_capture(session, settings):
    global _pool
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Analysis queue full')
    try:
        snapshot = {key: session.get(key) for key in INPUT_KEYS}
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
