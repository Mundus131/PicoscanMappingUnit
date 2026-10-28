import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class EncoderPollingTests(unittest.TestCase):
    def run_cycle(self, duration, failure=False, enabled=True, encoder_interval=None, packet=None):
        clock = [10.0]
        stop = Mock()
        stop.is_set.side_effect = [False, False, True] if packet is not None else [False, True]
        history = Mock()
        errors = []
        settings = {'enabled': enabled, 'poll_interval_ms': 50}
        if encoder_interval is not None:
            settings['encoder_poll_interval_ms'] = encoder_interval

        def fetch(**kwargs):
            clock[0] += duration
            if failure:
                raise TimeoutError()
            return {}

        ns = dict(
            device_manager=SimpleNamespace(
                motion_settings={'mode': 'encoder'},
                tdc_settings=settings),
            MotionHistory=lambda: history,
            time=SimpleNamespace(monotonic=lambda: clock[0]),
            tdc_rest=SimpleNamespace(fetch_encoder_process_data=fetch,
                tdc_token_manager=SimpleNamespace(get_token=lambda: 'test'),
                build_process_url=lambda _: 'http://localhost/test'),
            _extract_encoder_rpm=lambda _: 60,
            _calc_speed_from_encoder_rpm=lambda *_: 0.2,
            np=SimpleNamespace(isfinite=lambda _: True),
            _set_encoder_error=lambda session, code, message: errors.append(code),
        )
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_run_encoder_loop')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        self.session = {'encoder_stop_event': stop}
        source = Mock() if packet is not None else None
        if source is not None:
            source.receive.return_value = packet
        ns['_run_encoder_loop'](self.session, source)
        return stop.wait.call_args.args[0] if stop.wait.called else None, history, errors

    def test_process_timestamp_not_parent_delivery_time_is_used(self):
        _, history, errors = self.run_cycle(0, packet=dict(started=9.0, received=9.1,
            poll_gap_ms=200, payload={}))
        self.assertEqual(history.add.call_args.args, (9.1, 0.2))
        self.assertAlmostEqual(self.session['encoder_http_ms'], 100)
        self.assertAlmostEqual(self.session['encoder_delivery_ms'], 900)
        self.assertEqual(errors, [])

    def test_process_http_failure_invalidates_history(self):
        _, history, errors = self.run_cycle(0, packet=dict(started=9.0, received=9.5,
            poll_gap_ms=200, error='ReadTimeout'))
        self.assertEqual(history.add.call_args.args, (9.5, None))
        self.assertEqual(errors, ['encoder_read_failed'])
        self.assertIsNone(self.session['speed_mps'])

    def test_request_time_is_part_of_200ms_cycle(self):
        delay, _, errors = self.run_cycle(0.08)
        self.assertAlmostEqual(delay, 0.12)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(self.session['encoder_request_ms'], 80)

    def test_encoder_interval_is_independent_from_trigger_interval(self):
        delay, _, errors = self.run_cycle(0.02, encoder_interval=100)
        self.assertAlmostEqual(delay, 0.08)
        self.assertEqual(errors, [])

    def test_legacy_config_interval_cannot_exceed_freshness_budget(self):
        delay, _, _ = self.run_cycle(0.02, encoder_interval=1000)
        self.assertAlmostEqual(delay, 0.18)

    def test_config_schema_default_and_limits(self):
        from app.schemas.device import TdcSettings
        from pydantic import ValidationError
        config = dict(enabled=True, ip_address='192.168.0.100', port=8081,
                      login='test', password='test', trigger_input='DI_A', poll_interval_ms=100)
        self.assertEqual(TdcSettings(**config).encoder_poll_interval_ms, 200)
        for invalid in (0, 49, 201, 1000):
            with self.assertRaises(ValidationError):
                TdcSettings(**config, encoder_poll_interval_ms=invalid)

    def test_slow_response_remains_invalid_without_extra_delay(self):
        delay, history, errors = self.run_cycle(0.6)
        self.assertEqual(delay, 0)
        self.assertEqual(errors, ['encoder_latency'])
        self.assertAlmostEqual(self.session['encoder_request_max_ms'], 600)
        self.assertIsNone(history.add.call_args.args[1])

    def test_failure_records_gap_and_preserves_cadence(self):
        delay, history, errors = self.run_cycle(0.05, failure=True)
        self.assertAlmostEqual(delay, 0.15)
        self.assertEqual(errors, ['encoder_read_failed'])
        self.assertIsNone(history.add.call_args.args[1])

    def test_disabled_polling_waits_without_fetch(self):
        delay, _, errors = self.run_cycle(0.6, enabled=False)
        self.assertEqual(delay, 0.2)
        self.assertEqual(errors, ['encoder_disabled'])
