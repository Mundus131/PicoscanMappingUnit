"""Exercise the production receiver + manager worker without starting the web app.

Only this process is instrumented. No sensor/configuration writes are performed.
Run with backend/venv Python from the backend working directory.
"""
import argparse
import csv
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DEBUG'] = 'false'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind-ip', default='192.168.0.250')
    parser.add_argument('--port', type=int, default=2115)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--segments', type=int, default=10)
    parser.add_argument('--explicit-pattern', action='store_true', help='Use test IDs 0..segments-1 instead of production pattern learning')
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600 or not 1 <= args.port <= 65535 or not 1 <= args.segments <= 100:
        parser.error('Invalid duration/port/segment count')
    if Path.cwd().resolve() != (ROOT / 'backend').resolve():
        parser.error('Run from the backend directory to use its existing configuration')

    import app.services.picoscan_receiver as module
    import numpy as np

    errors = []
    class ErrorRecorder(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                errors.append(record.getMessage())
    module.logger.addHandler(ErrorRecorder())

    rows = []
    start = time.monotonic()
    class ObservedReceiver(module.PicoscanReceiver):
        def receive_segments(self, num_segments=1):
            result = super().receive_segments(num_segments)
            if result:
                for segment in result[0]:
                    modules = segment.get('Modules') or []
                    first = modules[0] if modules else {}
                    samples = positive = 0
                    for mod in modules:
                        for scan in mod.get('SegmentData', []):
                            values = np.asarray(scan.get('Distance', []))
                            samples += values.size
                            positive += int(np.count_nonzero(values > 0))
                    rows.append({
                        'host_elapsed_s': segment.get('_host_received_monotonic', time.monotonic())-start,
                        'frame': first.get('FrameNumber'), 'segment': first.get('SegmentCounter'),
                        'telegram': segment.get('TelegramCounter', first.get('TelegramCounter')),
                        'distance_samples': samples, 'positive_samples': positive,
                    })
            return result

    module.PicoscanReceiver = ObservedReceiver
    manager = module.PicoscanReceiverManager()
    device = 'diagnostic-only'
    captured = []
    output = ROOT / 'tools/picoscan_sdk_probe/output'
    output.mkdir(exist_ok=True)
    path = output / (time.strftime('%Y%m%d-%H%M%S') + '-backend')
    # Check endpoint exclusivity before the upstream socket is created.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as check:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        check.bind((args.bind_ip, args.port))
    start = time.monotonic()
    if not manager.start_listening(device, args.bind_ip, args.port, args.segments,
                                   segment_pattern=list(range(args.segments)) if args.explicit_pattern else None):
        raise RuntimeError('Cannot start diagnostic listener')
    info = manager.receivers[device]
    receiver = info['receiver']
    actual_buffer = receiver.transport_layer.client.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    def drain():
        for frame in manager.drain_point_clouds(device):
            captured.append({'frame': frame['device_frame'], 'points': len(frame['points'])})
    try:
        while time.monotonic()-start < args.seconds:
            drain()
            time.sleep(0.01)
        # Stop the real worker before reading counters or closing its socket.
        manager._worker_stops[device].set()
        manager._worker_threads[device].join(timeout=5)
        if manager._worker_threads[device].is_alive():
            raise RuntimeError('Receiver worker did not stop')
        drain()
        health = manager.get_health_snapshot()
        metrics = receiver.get_metrics()
    finally:
        manager.stop_listening(device)
    with path.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['host_elapsed_s','frame','segment','telegram','distance_samples','positive_samples'])
        writer.writeheader()
        writer.writerows(rows)
    frames = {}
    for row in rows:
        frames.setdefault(row['frame'], []).append(row['segment'])
    interior = list(frames)[1:-1]
    patterns = Counter(tuple(sorted(set(frames[f]))) for f in interior)
    span = rows[-1]['host_elapsed_s']-rows[0]['host_elapsed_s'] if len(rows)>1 else 0
    report = {
        'scope': 'Production UDP receiver, parser, frame assembler and queue; no HTTP/UI/encoder/measurement processing',
        'configured_segments': args.segments, 'explicit_pattern': args.explicit_pattern,
        'socket_receive_buffer': actual_buffer, 'received_segments': len(rows),
        'observed_frames': len(frames), 'interior_frames': len(interior),
        'observed_frame_rate_hz': (len(frames)-1)/span if span else None,
        'interior_segment_patterns': {','.join(map(str,k)):v for k,v in patterns.items()},
        'telegram_discontinuities': sum(b['telegram'] != a['telegram']+1 for a,b in zip(rows,rows[1:])
                                       if isinstance(a['telegram'],int) and isinstance(b['telegram'],int)),
        'delivered_complete_frames': len(captured),
        'delivered_points': sum(f['points'] for f in captured),
        'empty_delivered_frames': sum(f['points']==0 for f in captured),
        'counters': {k:info.get(k) for k in ('incomplete_frames_dropped','pattern_learning_frames_dropped',
                     'queue_overflows','late_complete_frames_dropped','device_frame_gaps','duplicate_segments','expected_segment_pattern')},
        'pending_frames_at_stop': len(info.get('frame_segments_pending',{})),
        'receiver_metrics': metrics, 'health': health,
        'warnings_errors': errors[:30], 'warning_error_count': len(errors),
        'captured_frames': captured,
    }
    path.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('captured_frames','health','receiver_metrics')}, indent=2))
    print('REPORT:', path.with_suffix('.json'))


if __name__ == '__main__':
    main()
