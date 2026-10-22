"""Quality propagation without hardware or running integration services."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.analysis import history_store
from app.analysis.measurement_quality import measurement_quality
from app.services.integration_mapper import build_active_bundle, build_catalog


class MeasurementQualityTests(unittest.TestCase):
    def test_unknown_is_not_valid_and_normalization_is_idempotent(self):
        quality = measurement_quality({})
        self.assertFalse(quality['measurement_valid'])
        self.assertEqual(quality['quality_status'], 'unknown')
        self.assertEqual(measurement_quality(quality), quality)

    def test_only_explicit_true_is_valid(self):
        for value in (None, 1, 'true'):
            self.assertEqual(measurement_quality({'measurement_valid': value})['quality_status'], 'unknown')
        self.assertTrue(measurement_quality({'measurement_valid': True})['measurement_valid'])
        self.assertEqual(measurement_quality({'measurement_valid': False})['quality_status'], 'invalid')

    def test_issues_and_encoder_errors_override_validity(self):
        for fields in ({'quality_issues': {'scanner_stale', 'encoder_stale'}},
                       {'encoder_error': 'timeout'}, {'encoder_error_message': 'Read failed'}):
            quality = measurement_quality({'measurement_valid': True, **fields})
            self.assertFalse(quality['measurement_valid'])
            self.assertEqual(quality['quality_status'], 'invalid')
            json.dumps(quality)
        self.assertEqual(measurement_quality({'quality_issues': ['b', 'a', 'b']})['quality_issues'], ['a', 'b'])

    def test_in_progress_never_valid(self):
        for state in ('recording', 'analysis_pending'):
            for valid in (True, False):
                quality = measurement_quality({'measurement_valid': valid, state: True})
                self.assertFalse(quality['measurement_valid'])
                self.assertEqual(quality['quality_status'], 'unknown')
            self.assertEqual(measurement_quality({state: True, 'quality_issues': ['encoder_stale']})['quality_status'], 'invalid')

    def test_captured_encoder_errors_are_frozen_without_mutating_live_diagnostics(self):
        session = {
            'measurement_valid': True, 'encoder_error': 'live_timeout',
            'encoder_error_message': 'Live read failed',
            'measurement_encoder_error': None, 'measurement_encoder_error_message': None,
        }
        self.assertEqual(measurement_quality(session)['quality_status'], 'valid')
        bundle = build_active_bundle(session, {}, 'UNKNOWN', 'log')
        result, diagnostics = bundle['snapshots']
        self.assertEqual(result['payload']['encoder_error'], '')
        self.assertEqual(diagnostics['payload']['encoder_error'], 'live_timeout')
        self.assertEqual(session['encoder_error'], 'live_timeout')
        self.assertEqual(measurement_quality({**session, 'recording': True})['quality_status'], 'invalid')
        session.update(encoder_error='', encoder_error_message='', measurement_encoder_error='captured_timeout',
                       measurement_encoder_error_message='Capture read failed')
        self.assertEqual(measurement_quality(session)['encoder_error'], 'captured_timeout')
        self.assertEqual(measurement_quality(session)['quality_status'], 'invalid')

    def test_result_readiness_and_numeric_quality_metadata(self):
        for app in ('log', 'conveyor_object'):
            for valid in (True, False, None):
                session = {'analysis_metrics': {'volume_mm3': 12.5}, 'measurement_valid': valid}
                result, diagnostics = build_active_bundle(session, {}, 'UNKNOWN', app)['snapshots']
                self.assertEqual(result['payload']['result_ready'], valid is True)
                ready = next(m for m in result['mqtt']['messages'] if m['topic'].endswith('/result_ready'))
                self.assertEqual(ready['payload'], valid is True)
                quality = measurement_quality(session)['quality_status']
                for meta in result['opcua']['nodes'].values():
                    if meta['data_type'] in ('Double', 'Int64'):
                        self.assertEqual(meta['quality_status'], quality)
                    else:
                        self.assertNotIn('quality_status', meta)
                self.assertTrue(all('quality_status' not in meta for meta in diagnostics['opcua']['nodes'].values()))

    def test_archive_save_list_detail_and_legacy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(history_store, 'HISTORY_DIR', directory):
            for payload in ({}, {'measurement_valid': True}, {
                'measurement_valid': False, 'quality_issues': {'encoder_stale'},
                'encoder_error': 'timeout', 'encoder_error_message': 'Read timed out',
            }):
                saved = history_store.save_measurement(payload)
                expected = measurement_quality(payload)
                for record in (saved, history_store.get_measurement(saved['id']), history_store.list_measurements()[0]):
                    for field, value in expected.items():
                        self.assertEqual(record[field], value)
            Path(directory, 'legacy.json').write_text(json.dumps({'id': 'legacy'}))
            self.assertEqual(history_store.get_measurement('legacy')['quality_status'], 'unknown')
            legacy = next(item for item in history_store.list_measurements() if item['id'] == 'legacy')
            self.assertFalse(legacy['measurement_valid'])

    def test_bundle_quality_reaches_mqtt_and_opcua_with_stable_types(self):
        for app in ('log', 'conveyor_object'):
            for session in ({}, {'measurement_valid': True}, {'measurement_valid': False, 'quality_issues': {'frames_skipped'}}):
                bundle = build_active_bundle(session, {}, 'UNKNOWN', app)
                for snapshot in bundle['snapshots']:
                    expected = measurement_quality(session)
                    topic = snapshot['mqtt']['topic']
                    root = snapshot['opcua']['root']
                    messages = {item['topic']: item['payload'] for item in snapshot['mqtt']['messages']}
                    for field, value in expected.items():
                        self.assertEqual(messages[f'{topic}/{field}'], value)
                        node = snapshot['opcua']['nodes'][f'{root}.{field}']
                        self.assertEqual(node['value'], json.dumps(value) if isinstance(value, list) else value)
                    self.assertEqual(snapshot['opcua']['nodes'][f'{root}.measurement_valid']['data_type'], 'Boolean')
        for app in build_catalog()['applications']:
            self.assertIn('encoder_error_message', app['mqtt_fields'])

    def test_mqtt_publishers_preserve_false_and_empty_values(self):
        root = Path(__file__).parents[2]
        for path in ('backend/app/services/mqtt_publisher.py', 'integrations/mqtt_publisher/main.py'):
            tree = ast.parse((root / path).read_text(encoding='utf-8-sig'))
            assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == 'payload' for target in node.targets)]
            self.assertEqual(len(assignments), 1)
            code = compile(ast.Expression(assignments[0].value), path, 'eval')
            for value in (False, True, None, 0, [], ''):
                self.assertEqual(json.loads(eval(code, {'json': json, 'item': {'payload': value}})), value)


if __name__ == '__main__':
    unittest.main()
