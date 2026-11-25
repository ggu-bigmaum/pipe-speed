"""Decode only original MVT attributes, never modify tile geometry."""
import gzip
import hashlib
import importlib
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def varint(n):
    b = bytearray()
    while n > 127:
        b.append((n & 127) | 128)
        n >>= 7
    return bytes(b + bytes([n]))


def field(n, value):
    if isinstance(value, int):
        return varint(n << 3) + varint(value)
    return varint((n << 3) | 2) + varint(len(value)) + value


def fixture(types=(2, 2), name='sa001', tags=b'\x00\x00\x01\x01'):
    features = b''.join(field(2, field(2, tags) + field(3, t) + field(4, b'\x09\x02\x04\x0a\x02\x04')) for t in types)
    layer = field(1, name.encode()) + features + field(3, b'PLINE_ID') + field(3, b'PLINE_CALB')
    layer += field(4, field(1, b'00123')) + field(4, bytes([25]) + struct.pack('<d', .25))
    layer += field(5, 4096) + field(15, 2)
    return field(3, layer)


def decoder():
    path = Path(__file__).resolve().parents[1] / 'comparison_mvt.py'
    assert path.exists(), 'Attribute-only MVT decoder is not implemented'
    return importlib.import_module('comparison_mvt')


def test_raw_and_gzip_preserve_feature_order_and_original_bytes():
    module = decoder()
    raw = fixture()
    for data in (raw, gzip.compress(raw)):
        tile = module.read_attribute_tile(data, 'sa001')
        assert tile.sha256 == hashlib.sha256(raw).hexdigest()
        assert list(tile.features()) == [(0, {'PLINE_ID':'00123','PLINE_CALB':.25}), (1, {'PLINE_ID':'00123','PLINE_CALB':.25})]


def test_type_zero_skipped_but_line_ordinals_are_contiguous():
    tile = decoder().read_attribute_tile(fixture((0, 2, 2)), 'sa001')
    assert [i for i, _ in tile.features()] == [0, 1]


def test_mixed_layers_and_wrong_geometry_fail_closed():
    module = decoder()
    with pytest.raises(ValueError): module.read_attribute_tile(fixture() + fixture(name='other'), 'sa001')
    with pytest.raises(ValueError): list(module.read_attribute_tile(fixture((1,)), 'sa001').features())


def test_malformed_lengths_tags_and_infinite_varints_fail_closed():
    module = decoder()
    for data in (b'\x1a\xff\xff\x7f', b'\x80'*20, fixture(tags=b'\x00'), fixture(tags=b'\x00\x7f')):
        with pytest.raises(ValueError):
            tile = module.read_attribute_tile(data, 'sa001')
            list(tile.features())


def test_decompression_limit_applies_before_materializing_whole_output():
    with pytest.raises(ValueError): decoder().read_attribute_tile(gzip.compress(b'x'*100), 'sa001', max_bytes=10)
