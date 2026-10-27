"""Vectorized Compact beam decoding; header parsing and CRC stay in ScanSegmentAPI."""
import numpy as np


def read_beam_data(data, metadata, offset):
    if not metadata['HasDistance']:
        return None
    layers = int(metadata['NumberOfLinesInModule'])
    echoes = int(metadata['NumberOfEchosPerBeam'])
    beams = int(metadata['NumberOfBeamsPerScan'])
    if min(layers, echoes, beams) <= 0:
        raise ValueError('Invalid Compact beam dimensions')
    fields = []
    for echo in range(echoes):
        fields.append((f'd{echo}', '<u2'))
        if metadata['HasRssi']:
            fields.append((f'r{echo}', '<u2'))
    if metadata['HasProperties']:
        fields.append(('properties', 'u1'))
    if metadata['HasTheta']:
        fields.append(('theta', '<u2'))
    dtype = np.dtype(fields, align=False)
    count = layers * beams
    if offset < 0 or count * dtype.itemsize > len(data) - offset:
        raise ValueError('Truncated Compact beam payload')
    # Wire order is beam, layer, then interleaved distance/RSSI echoes.
    records = np.frombuffer(data, dtype=dtype, count=count, offset=offset).reshape(beams, layers)
    result = []
    for layer in range(layers):
        rows = records[:, layer]
        result.append({
            'Distance': [rows[f'd{e}'].astype(np.float64) * metadata['DistanceScalingFactor']
                         for e in range(echoes)],
            'Rssi': [rows[f'r{e}'].astype(np.float64) if metadata['HasRssi']
                     else np.full(beams, np.nan) for e in range(echoes)],
            'Properties': rows['properties'].astype(np.float64) if metadata['HasProperties']
                          else np.full(beams, np.nan),
            'ChannelTheta': (rows['theta'].astype(np.float64) - 16384) / 5215.0
                            if metadata['HasTheta'] else np.full(beams, np.nan),
        })
    return {'SegmentData': result}
