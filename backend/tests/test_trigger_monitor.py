import ast
import logging
from pathlib import Path
import threading
import time
import types
import unittest
from unittest.mock import Mock
from app.services.trigger_guard import TriggerGuard


class TriggerMonitorTests(unittest.TestCase):
    def setUp(self):
        self.session = dict(recording=True, trigger_source='tdc', quality_issues=set())
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(tdc_input_state=2))
        def stop(app):
            self.session['recording'] = False
        self.api = types.SimpleNamespace(_get_session_from_app=lambda _: self.session,
            _session_lock=threading.RLock(), stop_trigger_session=Mock(side_effect=stop))
        ns = dict(logging=logging, threading=threading, time=time,
                  TriggerGuard=TriggerGuard, TdcDigitalIOClient=lambda: None,
                  acquisition_api=self.api, logger=logging.getLogger(__name__))
        path = Path(__file__).parents[1]/'app/services/tdc_trigger_monitor.py'
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        self.monitor = ns['TdcTriggerMonitor'](self.app)

    def test_fault_clears_high_marks_capture_and_stops_once(self):
        self.monitor._pending_start_at = 100
        self.monitor._pending_stop_target_mm = 200
        self.monitor._invalidate_input('trigger_input_stale')
        self.monitor._invalidate_input('trigger_input_stale')
        self.api.stop_trigger_session.assert_called_once()
        self.assertIsNone(self.app.state.tdc_input_state)
        self.assertIn('trigger_input_stale', self.session['quality_issues'])
        self.assertIsNone(self.monitor._pending_start_at)
        self.assertIsNone(self.monitor._pending_stop_target_mm)
        self.assertFalse(self.monitor._guard.armed)

    def test_fault_does_not_stop_manual_capture(self):
        self.session['trigger_source'] = 'manual'
        self.monitor._invalidate_input('trigger_input_invalid')
        self.api.stop_trigger_session.assert_not_called()

    def test_low_stops_tdc_capture_without_delay(self):
        self.monitor._handle_state(1, {'stop_delay_mode': 'time', 'stop_delay_ms': 0})
        self.api.stop_trigger_session.assert_called_once()

    def test_low_does_not_stop_manual_capture(self):
        self.session['trigger_source'] = 'manual'
        self.monitor._handle_state(1, {})
        self.api.stop_trigger_session.assert_not_called()
