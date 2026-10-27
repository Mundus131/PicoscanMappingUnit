import itertools
import struct
import unittest
import numpy as np
from app.services.compact_numpy import read_beam_data


def fixture(layers=2, echoes=3, beams=600, rssi=True, properties=True, theta=True):
    meta = dict(NumberOfLinesInModule=layers, NumberOfEchosPerBeam=echoes,
                NumberOfBeamsPerScan=beams, DistanceScalingFactor=0.25,
                HasDistance=True, HasRssi=rssi, HasProperties=properties, HasTheta=theta)
    raw = bytearray(b'prefix!')
    for beam in range(beams):
        for layer in range(layers):
            for echo in range(echoes):
                raw.extend(struct.pack('<H', beam + layer * 1000 + echo * 2000))
                if rssi:
                    raw.extend(struct.pack('<H', 10 + echo + layer))
            if properties:
                raw.extend(struct.pack('<B', beam % 256))
            if theta:
                raw.extend(struct.pack('<H', 16384 + beam))
    return bytes(raw), meta, 7


class CompactNumpyTests(unittest.TestCase):
    def test_all_channel_combinations_and_wire_order(self):
        for rssi, properties, theta in itertools.product((False, True), repeat=3):
            for layers, echoes in ((1, 1), (2, 3)):
                data, meta, offset = fixture(layers, echoes, rssi=rssi, properties=properties, theta=theta)
                result = read_beam_data(data, meta, offset)['SegmentData']
                for layer in range(layers):
                    for echo in range(echoes):
                        np.testing.assert_array_equal(result[layer]['Distance'][echo],
                            (np.arange(600) + layer * 1000 + echo * 2000) * 0.25)
                        np.testing.assert_array_equal(result[layer]['Rssi'][echo],
                            np.full(600, 10 + echo + layer if rssi else np.nan))
                    np.testing.assert_array_equal(result[layer]['Properties'],
                        np.arange(600) % 256 if properties else np.full(600, np.nan))
                    np.testing.assert_array_equal(result[layer]['ChannelTheta'],
                        np.arange(600) / 5215.0 if theta else np.full(600, np.nan))

    def test_truncated_and_invalid_payloads(self):
        data, meta, offset = fixture()
        with self.assertRaises(ValueError):
            read_beam_data(data[:-1], meta, offset)
        with self.assertRaises(ValueError):
            read_beam_data(data, meta, -1)
        meta['HasDistance'] = False
        self.assertIsNone(read_beam_data(data, meta, offset))

    def test_matches_installed_reference_parser(self):
        try:
            from scansegmentapi import compact
        except ImportError:
            self.skipTest('Optional upstream comparison requires ScanSegmentAPI')
        for rssi, properties, theta in itertools.product((False, True), repeat=3):
            data, meta, offset = fixture(rssi=rssi, properties=properties, theta=theta)
            reference = compact._read_beam_data(data, meta, offset)['SegmentData']
            actual = read_beam_data(data, meta, offset)['SegmentData']
            for expected_layer, actual_layer in zip(reference, actual):
                for key in expected_layer:
                    np.testing.assert_array_equal(actual_layer[key], expected_layer[key])
