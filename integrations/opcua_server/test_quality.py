"""Native asyncua address-space tests; no listening socket or hardware required."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from asyncua import Server, ua

spec = importlib.util.spec_from_file_location('quality_opcua_server', Path(__file__).with_name('main.py'))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class OpcuaQualityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        await self.server.init()
        namespace = await self.server.register_namespace('urn:quality-test')
        self.writer = adapter.BundleWriter(self.server.nodes.objects, namespace)

    def bundle(self, quality='valid'):
        return {'opcua_nodes': {
            'Applications.Log.volume_mm3': {'value': 42.5, 'data_type': 'Double', 'quality_status': quality},
            'Applications.Log.profiles_count': {'value': 7, 'data_type': 'Int64', 'quality_status': quality},
            'Applications.Log.result_ready': {'value': quality == 'valid', 'data_type': 'Boolean'},
            'Applications.Log.measurement_valid': {'value': quality == 'valid', 'data_type': 'Boolean'},
            'Applications.Log.quality_status': {'value': quality, 'data_type': 'String'},
            'Diagnostics.encoder_rpm': {'value': 8.5, 'data_type': 'Double'},
        }}

    async def value(self, name):
        return await self.writer.variables[name].read_data_value(raise_on_bad_status=False)

    async def test_quality_transitions_and_typed_diagnostics(self):
        for quality, status in [('valid', ua.StatusCodes.Good), ('invalid', ua.StatusCodes.BadDataLost),
                                ('unknown', ua.StatusCodes.Uncertain), ('valid', ua.StatusCodes.Good)]:
            await self.writer.apply(self.bundle(quality))
            for name in ('volume_mm3', 'profiles_count'):
                self.assertEqual((await self.value(f'Applications.Log.{name}')).StatusCode.value, status)
            for name, variant in [('Diagnostics.encoder_rpm', ua.VariantType.Double),
                                  ('Applications.Log.measurement_valid', ua.VariantType.Boolean),
                                  ('Applications.Log.result_ready', ua.VariantType.Boolean),
                                  ('Applications.Log.quality_status', ua.VariantType.String)]:
                data = await self.value(name)
                self.assertTrue(data.StatusCode.is_good())
                self.assertEqual(data.Value.VariantType, variant)
            self.assertEqual((await self.value('Applications.Log.result_ready')).Value.Value, quality == 'valid')

    async def test_missing_node_and_application_switch_invalidate_old_values(self):
        await self.writer.apply(self.bundle())
        bundle = self.bundle()
        del bundle['opcua_nodes']['Applications.Log.volume_mm3']
        await self.writer.apply(bundle)
        self.assertEqual((await self.value('Applications.Log.volume_mm3')).StatusCode.value, ua.StatusCodes.BadNoCommunication)
        await self.writer.apply({'opcua_nodes': {'Diagnostics.encoder_rpm': bundle['opcua_nodes']['Diagnostics.encoder_rpm']}})
        for name in self.writer.variables:
            expected = ua.StatusCodes.Good if name.startswith('Diagnostics.') else ua.StatusCodes.BadNoCommunication
            self.assertEqual((await self.value(name)).StatusCode.value, expected)

    async def test_fetch_failure_invalidates_every_node_then_recovers(self):
        await self.writer.apply(self.bundle())
        with patch.object(adapter, 'fetch_bundle', side_effect=ConnectionError('backend unavailable')):
            with self.assertRaises(ConnectionError):
                await self.writer.poll()
        for name in self.writer.variables:
            self.assertEqual((await self.value(name)).StatusCode.value, ua.StatusCodes.BadNoCommunication)
        with patch.object(adapter, 'fetch_bundle', return_value=self.bundle()):
            await self.writer.poll()
        for name in self.writer.variables:
            self.assertTrue((await self.value(name)).StatusCode.is_good())

    async def test_malformed_bundle_and_missing_quality_fail_closed(self):
        bundle = self.bundle()
        del bundle['opcua_nodes']['Applications.Log.volume_mm3']['quality_status']
        await self.writer.apply(bundle)
        self.assertEqual((await self.value('Applications.Log.volume_mm3')).StatusCode.value, ua.StatusCodes.Uncertain)
        with self.assertRaises(ValueError):
            await self.writer.apply({})
        for name in self.writer.variables:
            self.assertEqual((await self.value(name)).StatusCode.value, ua.StatusCodes.BadNoCommunication)

    async def test_partial_write_failure_does_not_leave_good_values(self):
        await self.writer.apply(self.bundle())
        real_write = adapter.set_variable_value

        async def fail_update(node, value, data_type, status_code=ua.StatusCodes.Good):
            if status_code != ua.StatusCodes.BadNoCommunication:
                raise RuntimeError('write failed')
            await real_write(node, value, data_type, status_code)

        with patch.object(adapter, 'set_variable_value', side_effect=fail_update):
            with self.assertRaises(RuntimeError):
                await self.writer.apply(self.bundle())
        for name in self.writer.variables:
            self.assertEqual((await self.value(name)).StatusCode.value, ua.StatusCodes.BadNoCommunication)


if __name__ == '__main__':
    unittest.main()
