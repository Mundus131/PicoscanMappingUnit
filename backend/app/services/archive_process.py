"""Bounded archive worker, independent of result calculation and API threads."""
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

_lock = threading.Lock()
_slots = threading.BoundedSemaphore(2)
_pool = None
STATE_KEYS = ('archive_pending', 'archive_last_error', 'archive_last_duration_ms',
              'archive_last_points_count', 'archive_last_ts')


def _archive(snapshot, payload):
    import numpy as np
    from app.analysis.history_store import save_measurement
    from app.services.raw_capture import archive_fields
    started = time.monotonic()
    if isinstance(payload.get('original_points'), np.ndarray):
        payload['original_points'] = payload['original_points'].tolist()
    payload.update(archive_fields(snapshot))
    profiles = snapshot.get('captured_profiles') or []
    payload['raw_positioned_points'] = np.vstack(profiles).tolist() if profiles else []
    payload['analysis_filter_settings'] = snapshot.get('analysis_filter_settings') or {}
    payload['relative_fragments'] = [dict(epoch=f['epoch'], position_status='relative_only',
        global_offset_mm=None, profiles_count=f['profiles_count'],
        profile_positions_mm=f['captured_profile_positions'],
        points=[p for profile in f['captured_profiles'] for p in profile.tolist()])
        for f in snapshot.get('relative_fragments') or []]
    save_measurement(payload)
    return dict(archive_pending=False, archive_last_error=None,
        archive_last_duration_ms=int((time.monotonic()-started)*1000),
        archive_last_points_count=len(payload.get('original_points') or []),
        archive_last_ts=int(time.time()*1000))


def queue_archive(session, payload):
    global _pool
    from app.services.analysis_process import INPUT_KEYS
    capture_id = session.get('capture_started_ts')

    def publish(state):
        session.update(state)
        live = session.get('archive_live_session')
        if live is not None and live.get('capture_started_ts') == capture_id:
            live.update(state)

    if not _slots.acquire(blocking=False):
        publish(dict(archive_pending=False, archive_last_error='Archive queue full; capture not archived.'))
        return
    try:
        snapshot = {key: session.get(key) for key in INPUT_KEYS}
        publish(dict(archive_pending=True, archive_last_error=None))
        with _lock:
            if _pool is None:
                _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
            pool = _pool
            future = pool.submit(_archive, snapshot, payload)
    except Exception:
        _slots.release()
        publish(dict(archive_pending=False, archive_last_error='Archive submission failed; capture not archived.'))
        with _lock:
            if _pool is not None:
                _pool.shutdown(wait=False, cancel_futures=True)
                _pool = None
        return

    def finished(task):
        global _pool
        try:
            publish(task.result())
        except Exception as exc:
            publish(dict(archive_pending=False, archive_last_error='Archive write failed: '+type(exc).__name__))
            if isinstance(exc, BrokenProcessPool):
                with _lock:
                    if _pool is pool:
                        pool.shutdown(wait=False, cancel_futures=True)
                        _pool = None
        finally:
            _slots.release()
    future.add_done_callback(finished)
