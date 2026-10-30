"""Quality flags must not suppress configured TCP frames or invent results."""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class TcpPublicationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'app/api/endpoints/acquisition.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_publish_analysis_tcp')
        self.notifier = Mock()
        self.manager = SimpleNamespace(output_settings={'enabled': True})
        self.frame = b'LOG;2600;150\r\n'
        self.flatten = Mock(return_value={'length': 2600, 'diameter_avg': 150})
        self.format = Mock(return_value=self.frame)
        ns = dict(device_manager=self.manager, _flatten_output_values=self.flatten,
                  _apply_output_units=lambda values, cfg: values,
                  _format_output_payload=self.format)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        self.publish = ns['_publish_analysis_tcp']
        self.app = SimpleNamespace(state=SimpleNamespace(tcp_notifier=self.notifier))
        self.session = dict(analysis_app='log', analysis_metrics={'total_length_mm': 2600},
                            measurement_valid=False,
                            quality_issues={'encoder_history_gap', 'encoder_speed_estimated'})

    def test_invalid_result_sends_unchanged_frame_without_mutating_quality(self):
        before = copy.deepcopy(self.session)
        self.publish(self.session, self.app)
        self.notifier.broadcast.assert_called_once_with(self.frame)
        self.assertEqual(self.session, before)
        self.assertEqual(self.format.call_args.args[0], {'length': 2600, 'diameter_avg': 150})

    def test_valid_result_still_sent(self):
        self.session.update(measurement_valid=True, quality_issues=set())
        self.publish(self.session, self.app)
        self.notifier.broadcast.assert_called_once_with(self.frame)

    def test_no_metrics_no_output(self):
        for metrics in (None, {}):
            self.session['analysis_metrics'] = metrics
            self.publish(self.session, self.app)
        self.notifier.broadcast.assert_not_called()

    def test_disabled_output_or_analysis_does_not_send(self):
        self.manager.output_settings['enabled'] = False
        self.publish(self.session, self.app)
        self.manager.output_settings['enabled'] = True
        self.session['analysis_app'] = 'none'
        self.publish(self.session, self.app)
        self.notifier.broadcast.assert_not_called()

    def test_no_notifier_is_safe(self):
        self.app.state.tcp_notifier = None
        self.publish(self.session, self.app)
        self.flatten.assert_not_called()
