"""Bounded attribute-only reader for a single delivered line MVT layer.

Geometry command bytes are skipped. The order is the decoder's original line
feature order, not the renderer's subsequently sorted feature array.
"""
import gzip
import hashlib
import io
import struct
from dataclasses import dataclass


def hash_tile_stream(source, max_bytes=256 * 1024 * 1024):
    """Hash decompressed content in 1 MiB chunks; do not retain the tile."""
    magic = source.read(2)
    source.seek(0)
    compressed = magic == b'\x1f\x8b'
    reader = gzip.GzipFile(fileobj=source) if compressed else source
    digest, size = hashlib.sha256(), 0
    try:
        while True:
            block = reader.read(min(1024 * 1024, max_bytes - size + 1))
            if not block:
                break
            size += len(block)
            if size > max_bytes:
                raise ValueError('Tile exceeds comparison sidecar size limit')
            digest.update(block)
    except (OSError, EOFError) as error:
        raise ValueError('Invalid tile content') from error
    finally:
        if compressed:
            reader.close()
    return digest.hexdigest()


def _varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError('Truncated protobuf integer')
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError('Oversized protobuf integer')
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ValueError('Invalid protobuf integer')


def _fields(data):
    position = 0
    while position < len(data):
        header, position = _varint(data, position)
        number, wire = header >> 3, header & 7
        if not number:
            raise ValueError('Invalid protobuf field')
        if wire == 0:
            value, position = _varint(data, position)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, position = _varint(data, position)
            else:
                size = 8 if wire == 1 else 4
            if position + size > len(data):
                raise ValueError('Truncated protobuf field')
            value = data[position:position + size]
            position += size
        else:
            raise ValueError('Unsupported protobuf wire type')
        yield number, wire, value


def _value(data):
    fields = list(_fields(data))
    if len(fields) != 1:
        raise ValueError('Ambiguous MVT value')
    number, wire, value = fields[0]
    if number == 1 and wire == 2:
        return bytes(value).decode('utf-8')
    if number == 2 and wire == 5:
        return struct.unpack('<f', value)[0]
    if number == 3 and wire == 1:
        return struct.unpack('<d', value)[0]
    if wire == 0:
        if number == 4:
            return value - 2**64 if value >= 2**63 else value
        if number == 5:
            return value
        if number == 6:
            return (value >> 1) ^ -(value & 1)
        if number == 7:
            return bool(value)
    raise ValueError('Unsupported MVT value')


@dataclass
class AttributeTile:
    layer: memoryview
    keys: list
    values: list
    sha256: str

    def features(self):
        ordinal = 0
        for number, wire, feature in _fields(self.layer):
            if number != 2:
                continue
            if wire != 2:
                raise ValueError('Invalid MVT feature')
            kind, tags = 0, memoryview(b'')
            for field, field_wire, value in _fields(feature):
                if field == 3:
                    if field_wire != 0:
                        raise ValueError('Invalid MVT geometry type')
                    kind = value
                elif field == 2:
                    if field_wire != 2:
                        raise ValueError('Invalid MVT tags')
                    tags = value
            if kind == 0:
                continue  # OpenLayers does not create a feature for UNKNOWN.
            if kind != 2:
                raise ValueError('Comparison sidecar supports line tiles only')
            position, properties = 0, {}
            while position < len(tags):
                key, position = _varint(tags, position)
                value, position = _varint(tags, position)
                if key >= len(self.keys) or value >= len(self.values):
                    raise ValueError('Invalid MVT tag index')
                properties[self.keys[key]] = self.values[value]
            yield ordinal, properties
            ordinal += 1


def tile_content(data, max_bytes=256 * 1024 * 1024):
    """Canonical uncompressed bytes, without decoding attributes or geometries."""
    if len(data) > max_bytes:
        raise ValueError('Compressed tile exceeds comparison size limit')
    if data[:2] == b'\x1f\x8b':
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
                data = source.read(max_bytes + 1)
        except (OSError, EOFError) as error:
            raise ValueError('Invalid gzip tile') from error
    if len(data) > max_bytes:
        raise ValueError('Tile exceeds comparison sidecar size limit')
    return data


def read_attribute_tile(data, expected_layer, max_bytes=256 * 1024 * 1024):
    data = tile_content(data, max_bytes)
    layers = [value for number, wire, value in _fields(memoryview(data)) if number == 3 and wire == 2]
    if len(layers) != 1:
        raise ValueError('Comparison sidecar requires exactly one layer')
    name, keys, values = None, [], []
    for number, wire, value in _fields(layers[0]):
        if number == 1 and wire == 2:
            name = bytes(value).decode('utf-8')
        elif number == 3 and wire == 2:
            keys.append(bytes(value).decode('utf-8'))
        elif number == 4 and wire == 2:
            values.append(_value(value))
    if name != expected_layer:
        raise ValueError('Unexpected MVT layer')
    return AttributeTile(layers[0], keys, values, hashlib.sha256(data).hexdigest())
