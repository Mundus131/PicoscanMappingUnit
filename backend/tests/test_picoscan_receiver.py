"""Receiver contracts, isolated from hardware and application startup."""
import ast
import builtins
from collections import deque
import io
import logging
from pathlib import Path
import re
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np


class LibraryPrintTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/services/picoscan_receiver.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        nodes = [node for node in tree.body
                 if (isinstance(node, ast.FunctionDef) and node.name == '_scansegment_print')
                 or (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                     and node.value.id == '_scansegment_print')]
        self.logger = Mock()
        self.modules = [types.ModuleType('compact'), types.ModuleType('msgpack')]
        namespace = dict(builtins=builtins, re=re, logger=self.logger,
                         CompactApi=self.modules[0], MsgpackApi=self.modules[1])
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)

    def test_exact_progress_messages_go_to_debug_in_both_modules(self):
        original_print = builtins.print
        for module in self.modules:
            # Exercise module global lookup, as the real receive functions do.
            exec('def emit():\n    print("Received segment 12.")', module.__dict__)
            with patch('builtins.print') as forwarded:
                module.emit()
                forwarded.assert_not_called()
        self.assertIs(builtins.print, original_print)
        self.assertEqual(self.logger.debug.call_count, 2)
        self.logger.debug.assert_called_with('%s', 'Received segment 12.')

    def test_other_messages_and_print_options_are_forwarded_unchanged(self):
        sink = io.StringIO()
        cases = [((text,), {}) for text in (
            'Failed to extract payload from data.', 'Received segment 1.\n',
            'Received segment -1.', 'Received segment 1. extra', 'Received segment x.')]
        cases.extend([((), {}), (('Received segment 1.',), {'file': sink}),
                      (('error', 7), {'sep': ':', 'end': '', 'file': sink, 'flush': True})])
        for module in self.modules:
            for args, kwargs in cases:
                with patch('builtins.print') as forwarded:
                    module.print(*args, **kwargs)
                    forwarded.assert_called_once_with(*args, **kwargs)
        self.logger.debug.assert_not_called()


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        clock = types.SimpleNamespace(time=lambda: 1000 + self.now,
                                      monotonic=lambda: self.now,
                                      perf_counter=lambda: self.now)
        device_manager = types.SimpleNamespace(point_cloud_settings={}, get_device=lambda _: None)
        self.ns = dict(time=clock, np=np, deque=deque, threading=threading,
                       Optional=__import__('typing').Optional, Tuple=tuple, List=list,
                       logger=logging.getLogger(__name__), device_manager=device_manager,
                       CAPTURE_QUEUE_MAX=256, PIPELINE_TIMING_WINDOW=120,
                       PATTERN_LOCK_FRAMES=5,
                       PATTERN_OBSERVATION_WINDOW=40,
                       Lms4000Receiver=type('LmdBase', (), {
                           '_parse_lmdscandata': lambda _, telegram: np.ones((1, 4))}))
        path = Path(__file__).parents[1] / 'app/services/picoscan_receiver.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        nodes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name in ('PicoscanReceiver', 'PicoscanReceiverManager', '_TimestampedLmdReceiver')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.ns)
        self.manager = self.ns['PicoscanReceiverManager']()
        self.receiver = types.SimpleNamespace(connected=True, segments_to_point_cloud=lambda parts:
            np.array([[s.get('value', 1), 0, 0, 0] for s in parts]))
        self.info = self.manager._build_receiver_info(
            receiver=self.receiver, receiver_type='picoscan', device_id='a', device_type='picoscan',
            format_type='compact', listen_ip='0.0.0.0', listen_port=2115, segments_per_scan=2,
            fallback_enabled=False, fallback_listen_ip=None, fallback_listen_port=None,
            fallback_formats=[], segment_pattern=[0, 1])
        self.manager.receivers['a'] = self.info

    def segment(self, frame, counter, **extra):
        return dict(Modules=[dict(FrameNumber=frame, SegmentCounter=counter)], **extra)

    def test_parser_backlog_does_not_expire_timely_received_segments(self):
        first = self.segment(20, 0, _host_received_wall=1001,
                             _host_received_monotonic=1.0)
        second = self.segment(20, 1, _host_received_wall=1001.01,
                              _host_received_monotonic=1.01)
        self.assertEqual(self.manager._assemble_complete_frame_segments(self.info, [first]), [])
        result = self.manager._assemble_complete_frame_segments(self.info, [second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['received_monotonic'], 1.0)
        self.assertEqual(self.info['incomplete_frames_dropped'], 0)

    def test_closed_frame_index_stays_bounded_and_tracks_eviction(self):
        for frame in range(4100):
            self.manager._assemble_complete_frame_segments(self.info,
                [self.segment(frame, 0), self.segment(frame, 1)])
        self.assertEqual(len(self.info['closed_frame_index']), 4096)
        self.assertEqual(self.info['closed_frame_index'], set(self.info['closed_frames']))
        self.assertNotIn(0, self.info['closed_frame_index'])
        before = self.info['duplicate_segments']
        self.manager._assemble_complete_frame_segments(self.info, [self.segment(4099, 0)])
        self.assertEqual(self.info['duplicate_segments'], before + 1)

    def test_unset_device_segment_count_uses_global_default(self):
        for global_count, device_count, expected in ((10, None, 10), (5, None, 5), (10, 2, 2)):
            with self.subTest(global_count=global_count, device_count=device_count):
                self.ns['device_manager'].point_cloud_settings = {'segments_per_scan': global_count}
                info = self.manager._build_receiver_info(
                    receiver=self.receiver, receiver_type='picoscan', device_id='b', device_type='picoscan',
                    format_type='compact', listen_ip='0.0.0.0', listen_port=2115,
                    segments_per_scan=device_count, fallback_enabled=False,
                    fallback_listen_ip=None, fallback_listen_port=None, fallback_formats=[])
                self.assertEqual(info['segments_per_scan_hint'], expected)

    def test_unset_device_count_can_learn_and_deliver_complete_frames(self):
        self.ns['device_manager'].point_cloud_settings = {'segments_per_scan': 2}
        info = self.manager._build_receiver_info(
            receiver=self.receiver, receiver_type='picoscan', device_id='a', device_type='picoscan',
            format_type='compact', listen_ip='0.0.0.0', listen_port=2115,
            segments_per_scan=None, fallback_enabled=False,
            fallback_listen_ip=None, fallback_listen_port=None, fallback_formats=[])
        self.info = info
        self.manager.receivers['a'] = info
        for frame in range(5):
            self.feed(self.segment(frame, 0), self.segment(frame, 1))
            self.now += .4
            self.feed()
        self.assertEqual([f['device_frame'] for f in self.drain()], [4])
        self.assertEqual(info['incomplete_frames_dropped'], 0)

    def feed(self, *segments):
        return self.manager._convert_completed_frames(self.info, self.receiver, list(segments))

    def drain(self):
        return self.manager.drain_point_clouds('a')

    def test_every_frame_in_batch_and_drain_once(self):
        self.feed(*(self.segment(f, c) for f in range(4) for c in range(2)))
        frames = self.drain()
        self.assertEqual([f['frame'] for f in frames], [1, 2, 3, 4])
        self.assertTrue(all(f['complete'] and len(f['points']) == 2 for f in frames))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.manager.drain_point_clouds('missing'), [])

    def test_first_segment_timestamp_and_out_of_order(self):
        self.feed(self.segment(1, 1, _host_received_wall=900., _host_received_monotonic=9.9))
        self.feed(self.segment(2, 0), self.segment(2, 1), self.segment(1, 0))
        frames = self.drain()
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]['timestamp'], 900.)
        self.assertEqual(frames[0]['received_monotonic'], 9.9)
        self.assertEqual(self.info['incomplete_frames_dropped'], 0)

    def test_duplicates_do_not_complete_or_replace_or_reemit(self):
        self.feed(self.segment(1, 0, value=5), self.segment(1, 0, value=99))
        self.assertEqual(self.drain(), [])
        self.feed(self.segment(1, 1))
        self.assertEqual(self.drain()[0]['points'][0, 0], 5)
        self.feed(self.segment(1, 0), self.segment(1, 1))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['duplicate_segments'], 3)

    def test_timeout_without_traffic_and_no_adaptive_shrinking(self):
        for frame in range(10):
            self.feed(self.segment(frame, 0))
            self.now += .4
            self.feed()
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['incomplete_frames_dropped'], 10)
        self.assertEqual(self.info['expected_segment_pattern'], [0, 1])

    def test_missing_and_invalid_metadata_never_complete(self):
        self.ns['device_manager'].point_cloud_settings['require_complete_frames'] = False
        self.feed({}, self.segment(1, 9), self.segment(1, 0), self.segment(1, 1))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['incomplete_frames_dropped'], 2)

    def test_msgpack_metadata_and_explicit_fullframe_batches(self):
        self.feed(dict(FrameNumber=1, SegmentCounter=0), dict(FrameNumber=1, SegmentCounter=1),
                  self.segment(2, -1), self.segment(3, -1))
        self.assertEqual(len(self.drain()), 3)

    def test_overflow_is_bounded_and_reported(self):
        self.feed(*(self.segment(f, -1) for f in range(260)))
        health = self.manager.get_health_snapshot()['a']
        self.assertEqual(health['queue_overflows'], 4)
        self.assertEqual(health['incomplete_frames_dropped'], 0)
        frames = self.drain()
        self.assertEqual(len(frames), 256)
        self.assertEqual(frames[0]['frame'], 5)

    def test_preview_does_not_consume_queue(self):
        points = self.feed(self.segment(1, -1))
        self.info.update(latest_points=points, latest_update_ts=1010.)
        preview = self.manager.get_latest_point_cloud('a')
        preview['points'][:] = 99
        self.assertEqual(self.drain()[0]['points'][0, 0], 1)

    def test_worker_queues_all_frames_and_keeps_latest_preview(self):
        stop = threading.Event()

        def receive(_):
            stop.set()
            return [self.segment(1, -1, value=1), self.segment(2, -1, value=2)]

        self.receiver.receive_segments = receive
        self.manager._worker_loop('a', stop)
        self.assertEqual(len(self.drain()), 2)
        self.assertEqual(self.manager.get_latest_point_cloud('a')['points'][0, 0], 2)

    def test_unconfigured_count_never_learns_completeness(self):
        self.info['segments_per_scan_hint'] = None
        self.info['expected_segment_pattern'] = None
        for frame in range(10):
            self.feed(self.segment(frame, 0))
        self.assertEqual(self.drain(), [])
        self.assertIsNone(self.info['expected_segment_pattern'])

    def test_lower_receiver_preserves_preparse_receipts_and_partial_batch(self):
        receiver = self.ns['PicoscanReceiver']()
        transport = types.SimpleNamespace(last_host_receipt=None)
        calls = []

        def read(count):
            calls.append(count)
            if len(calls) == 3:
                raise ValueError('bad packet')
            transport.last_host_receipt = (100. + len(calls), float(len(calls)))
            self.now += 1  # Simulated parsing latency must not affect receipt.
            return [self.segment(1, len(calls) - 1)], [1], [len(calls) - 1]

        receiver.connected = True
        receiver.transport_layer = transport
        receiver.receiver = types.SimpleNamespace(transport_layer=transport, receive_segments=read)
        with self.assertLogs(level='ERROR'):
            result = receiver.receive_segments(3)
        self.assertEqual(len(result[0]), 2)
        self.assertEqual([s['_host_received_monotonic'] for s in result[0]], [1., 2.])
        self.assertEqual([s['_host_received_wall'] for s in result[0]], [101., 102.])

    def test_late_completion_is_rejected_across_drains(self):
        self.feed(self.segment(1, 0))
        self.now += .1
        self.feed(self.segment(2, -1))
        self.assertEqual(self.drain()[0]['device_frame'], 2)
        self.feed(self.segment(1, 1))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.manager.get_health_snapshot()['a']['late_complete_frames_dropped'], 1)

    def test_wholly_missing_frames_and_device_rewinds_are_visible(self):
        self.feed(self.segment(10, -1), self.segment(13, -1))
        self.assertEqual([f['device_frame'] for f in self.drain()], [10, 13])
        self.feed(self.segment(12, -1))
        self.assertEqual(self.drain(), [])
        health = self.manager.get_health_snapshot()['a']
        self.assertEqual(health['device_frame_gaps'], 2)
        self.assertEqual(health['device_frame_regressions'], 1)

    def test_learning_nonzero_pattern_requires_five_closed_full_count_frames(self):
        self.info.update(expected_segment_pattern=None, pattern_locked=False)
        for frame in range(5):
            self.feed(self.segment(frame, 7), self.segment(frame, 8))
            self.assertEqual(self.drain(), [])
            self.now += .4
            self.feed()
            if frame < 4:
                self.assertEqual(self.drain(), [])
                self.assertFalse(self.info['pattern_locked'])
        self.assertEqual(self.info['expected_segment_pattern'], [7, 8])
        self.assertEqual(len(self.drain()), 1)
        self.assertEqual(self.info['pattern_learning_frames_dropped'], 4)
        # Once locked, changing patterns cannot relock or shrink the count.
        for frame in range(5, 15):
            self.feed(self.segment(frame, 2), self.segment(frame, 3))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['expected_segment_pattern'], [7, 8])

    def test_partial_patterns_never_lock(self):
        self.info.update(expected_segment_pattern=None, pattern_locked=False)
        for frame in range(8):
            self.feed(self.segment(frame, 7))
            self.now += .4
            self.feed()
        self.assertFalse(self.info['pattern_locked'])
        self.assertEqual(self.info['incomplete_frames_dropped'], 8)

    def test_learning_transition_releases_buffered_complete_frames_before_newer_frame(self):
        self.info.update(expected_segment_pattern=None, pattern_locked=False)
        for frame in range(4):
            self.feed(self.segment(frame, 7), self.segment(frame, 8))
            self.now += .4
            self.feed()
        for frame in range(4, 7):
            self.feed(self.segment(frame, 7), self.segment(frame, 8))
            self.now += .1
        self.now += .06  # Only frame 4 has reached the learning timeout.
        self.feed(self.segment(7, 7))
        self.assertEqual([f['device_frame'] for f in self.drain()], [4, 5, 6])
        self.feed(self.segment(7, 8))
        self.assertEqual([f['device_frame'] for f in self.drain()], [7])
        self.now += .4
        self.feed()
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['late_complete_frames_dropped'], 0)
        self.assertEqual(self.info['device_frame_gaps'], 0)

    def prepare_final_learning_frame(self):
        self.info.update(expected_segment_pattern=None, pattern_locked=False)
        for frame in range(4):
            self.feed(self.segment(frame, 7), self.segment(frame, 8))
            self.now += .4
            self.feed()
        self.feed(self.segment(4, 7), self.segment(4, 8))
        self.assertEqual(self.drain(), [])
        self.now += .4

    def test_worker_no_data_queues_timeout_learning_completion(self):
        self.prepare_final_learning_frame()
        stop = threading.Event()

        def receive(_):
            stop.set()
            return None

        self.receiver.receive_segments = receive
        self.manager._worker_loop('a', stop)
        frames = self.drain()
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['device_frame'], 4)
        self.assertTrue(frames[0]['complete'])
        self.assertEqual(len(self.manager.get_latest_point_cloud('a')['points']), 2)

    def test_fallback_no_data_queues_timeout_learning_completion(self):
        self.prepare_final_learning_frame()
        self.info['fallback_enabled'] = True
        self.receiver.receive_segments = lambda _: None
        self.manager._ensure_fallback_receiver = lambda _: self.receiver
        points = self.manager._try_scansegment_fallback_points(self.info)
        self.assertEqual(len(points), 2)
        frames = self.drain()
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['device_frame'], 4)

    def test_capture_quality_counters_are_exposed_in_health(self):
        expected = dict(late_complete_frames_dropped=2, device_frame_gaps=3,
                        pattern_learning_frames_dropped=4)
        self.info.update(expected)
        health = self.manager.get_health_snapshot()['a']
        for key, value in expected.items():
            self.assertEqual(health[key], value)

    def test_different_full_patterns_do_not_lock(self):
        self.info.update(expected_segment_pattern=None, pattern_locked=False)
        for frame in range(10):
            start = 7 if frame % 2 else 2
            self.feed(self.segment(frame, start), self.segment(frame, start + 1))
            self.now += .4
            self.feed()
        self.assertFalse(self.info['pattern_locked'])
        self.assertEqual(self.drain(), [])

    def test_explicit_nonzero_pattern_needs_no_learning(self):
        self.info['expected_segment_pattern'] = [7, 9]
        self.feed(self.segment(1, 9), self.segment(1, 7))
        self.assertEqual(self.drain()[0]['device_frame'], 1)
        self.assertEqual(self.info['pattern_learning_frames_dropped'], 0)

    def test_malformed_metadata_does_not_qualify(self):
        self.feed(None, self.segment(1, .5), self.segment(-1, 0))
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.info['incomplete_frames_dropped'], 3)

    def test_batch_parse_latency_does_not_expire_timely_segments(self):
        self.feed(self.segment(1, 0, _host_received_monotonic=10.))
        self.now = 11.
        self.feed(self.segment(1, 1, _host_received_monotonic=10.1))
        self.assertEqual(len(self.drain()), 1)
        self.assertEqual(self.info['incomplete_frames_dropped'], 0)

    def test_lmd_worker_queues_whole_scan_with_preparse_timestamp(self):
        stop = threading.Event()
        lower = self.ns['_TimestampedLmdReceiver']()

        def receive(count):
            self.assertEqual(count, 1)
            points = lower._parse_lmdscandata('sSN LMDscandata')
            self.now += .1
            self.receiver.last_frame_receipt = lower.last_frame_receipt
            stop.set()
            return points

        self.info['format_type'] = 'lmdscandata'
        self.receiver.receive_point_cloud = receive
        self.manager._worker_loop('a', stop)
        frame = self.drain()[0]
        self.assertTrue(frame['complete'])
        self.assertEqual(frame['received_monotonic'], 10.)
        self.assertEqual(frame['timestamp_source'], 'host_telegram_preparse')
        self.assertIsNone(frame['device_frame'])
        self.assertFalse(self.manager.get_health_snapshot()['a']['device_frame_tracking_supported'])

    def test_blocking_reads_on_two_receivers_are_not_serialized(self):
        barrier = threading.Barrier(2)
        results = []

        def receive(_):
            barrier.wait(timeout=2.)
            return [self.segment(1, 0)], [1], [0]

        receivers = [self.ns['PicoscanReceiver']() for _ in range(2)]
        for receiver in receivers:
            receiver.connected = True
            receiver.transport_layer = types.SimpleNamespace(last_host_receipt=None)
            receiver.receiver = types.SimpleNamespace(
                transport_layer=receiver.transport_layer, receive_segments=receive)
        threads = [threading.Thread(target=lambda r=r: results.append(r.receive_segments(1)))
                   for r in receivers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(results))


if __name__ == '__main__':
    unittest.main()
