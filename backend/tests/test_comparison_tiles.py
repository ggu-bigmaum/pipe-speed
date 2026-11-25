"""Strict flags are bounded sidecars over unchanged original MVT bytes."""
import base64
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import threading

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_comparison_api import Package
from test_comparison_mvt import field, varint


def attrs(identifier):
    return {'PLINE_ID': identifier, 'PLINE_CALB': 100, 'PLINE_MTRQ': 'MOP001'}


def tile(properties):
    keys = ['PLINE_ID', 'PLINE_CALB', 'PLINE_MTRQ']
    values, features = [], []
    for properties in properties:
        tags = bytearray()
        for index, name in enumerate(keys):
            value = properties.get(name)
            if value is None:
                continue
            tags.extend(varint(index) + varint(len(values)))
            values.append(field(1, value.encode()) if isinstance(value, str) else field(5, value))
        features.append(field(2, field(2, bytes(tags)) + field(3, 2) + field(4, b'\x09\x02\x04\x0a\x02\x04')))
    layer = field(1, b'sa001') + b''.join(features)
    layer += b''.join(field(3, key.encode()) for key in keys)
    layer += b''.join(field(4, value) for value in values)
    return field(3, layer + field(5, 4096) + field(15, 2))


@pytest.fixture
def setup(tmp_path):
    data = tmp_path / 'data'
    directory = data / 'comparison' / 'water'
    directory.parent.mkdir(parents=True)
    package = Package(directory)
    package.pipe('seoul', 20, 'S1-duplicate', attrs('S1'), strict=False)
    with sqlite3.connect(package.path) as db:
        db.execute("UPDATE pipes SET exact_same_id_geometry=0 WHERE dataset='seoul' AND source_fid=2")
        db.execute('INSERT INTO tile_groups SELECT dataset,tile_key,COUNT(*),MIN(exact_same_id_geometry) FROM pipes GROUP BY dataset,tile_key')
    package.finish()
    return data, package


def write_tile(data, properties, *, z=10, x=873, y=396, source='tiles_full'):
    path = data / source / 'sa001' / str(z) / str(x) / (str(y) + '.pbf')
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = tile(properties)
    path.write_bytes(gzip.compress(raw))
    return path, raw


def app(data):
    assert (Path(__file__).resolve().parents[1] / 'comparison_tiles.py').exists(), 'Strict tile sidecar API is not implemented'
    module = importlib.import_module('comparison_tiles')
    application = Flask(__name__)
    application.register_blueprint(module.create_comparison_tiles_blueprint(data))
    return application


URL = '/comparison/water/tiles/sa001/10/873/396'


def test_flags_follow_original_order_and_require_every_group_member_strict(setup):
    data, package = setup
    path, raw = write_tile(data, [attrs('S3'), attrs('S1'), attrs('S2'), attrs('S3')])
    before = path.read_bytes()
    response = app(data).test_client().get(URL)
    assert response.status_code == 200
    result = response.get_json()
    assert result['feature_count'] == 4 and result['matched_count'] == 2
    assert base64.b64decode(result['bits_base64']) == b'\x09'
    assert result['sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['schema_version'] == 1 and package.sha in result['version']
    assert result['layer'] == 'sa001' and result['available'] is True
    assert path.read_bytes() == before


def test_unknown_key_fails_closed_instead_of_returning_partial_flags(setup):
    data, _ = setup
    write_tile(data, [attrs('S3'), attrs('unmapped')])
    result = app(data).test_client().get(URL)
    assert result.status_code == 503 and result.get_json()['available'] is False
    assert 'bits_base64' not in result.get_json()


def test_empty_tile_has_empty_flags(setup):
    data, _ = setup
    write_tile(data, [])
    result = app(data).test_client().get(URL).get_json()
    assert result['feature_count'] == result['matched_count'] == 0 and result['bits_base64'] == ''


@pytest.mark.parametrize('url', [
    '/comparison/water/tiles/kict_national/10/873/396',
    '/comparison/water/tiles/sa001/-1/0/0', '/comparison/water/tiles/sa001/18/0/0',
    '/comparison/water/tiles/sa001/10/1024/0', '/comparison/water/tiles/sa001/10/0/-1',
    '/comparison/water/tiles/sa001/no/0/0', '/comparison/water/tiles/sa001/10/873/397',
])
def test_unsupported_or_missing_tiles_are_json_404(setup, url):
    data, _ = setup
    response = app(data).test_client().get(url)
    assert response.status_code == 404 and response.is_json


def test_path_resolution_matches_full_zoom_preference_and_legacy_fallback(setup):
    data, _ = setup
    write_tile(data, [attrs('S3')])
    write_tile(data, [attrs('S3'), attrs('S3')], source='tiles_low')
    write_tile(data, [attrs('S3')], z=11, x=1746, y=793, source='tiles_low')
    write_tile(data, [attrs('S3')], z=12, x=3492, y=1586, source='tiles')
    # A full zoom directory means a missing full tile stays missing.
    write_tile(data, [attrs('S3')], y=397, source='tiles_low')
    client = app(data).test_client()
    assert client.get(URL).get_json()['feature_count'] == 1
    assert client.get('/comparison/water/tiles/sa001/10/873/397').status_code == 404
    assert client.get('/comparison/water/tiles/sa001/11/1746/793').status_code == 200
    assert client.get('/comparison/water/tiles/sa001/12/3492/1586').status_code == 200


def test_tile_change_and_package_version_change_invalidate_cache(setup):
    data, package = setup
    write_tile(data, [attrs('S3')])
    client = app(data).test_client()
    first = client.get(URL).get_json()
    write_tile(data, [attrs('S3'), attrs('S3')])
    second = client.get(URL).get_json()
    assert second['feature_count'] == 2 and first['sha256'] != second['sha256']
    with sqlite3.connect(package.path) as db:
        db.execute("UPDATE tile_groups SET all_strict=0 WHERE dataset='seoul'")
    package.finish()
    third = client.get(URL).get_json()
    assert third['version'] != second['version'] and third['matched_count'] == 0


def test_decoder_and_read_size_are_bounded(setup, monkeypatch):
    data, _ = setup
    write_tile(data, [attrs('S3')] * 30)
    application = app(data)
    module = importlib.import_module('comparison_tiles')
    monkeypatch.setattr(module, 'MAX_TILE_BYTES', 100)
    assert application.test_client().get(URL).status_code == 503


def test_corrupt_deflate_returns_json_and_releases_build_slot(setup):
    data, _ = setup
    path, _ = write_tile(data, [attrs('S3')])
    path.write_bytes(b'\x1f\x8b\x08\x00' + b'\x00' * 6 + b'\x06' + b'\x00' * 8)
    client = app(data).test_client()
    response = client.get(URL)
    assert response.status_code == 503 and response.is_json
    write_tile(data, [attrs('S3')])
    assert client.get(URL).status_code == 200


def test_chunk_queries_are_bounded_and_duplicate_features_keep_their_bits(setup, monkeypatch):
    data, _ = setup
    write_tile(data, [attrs('S' + str(i)) for i in range(1, 9)] + [attrs('S3')])
    application = app(data)
    module = importlib.import_module('comparison_tiles')
    monkeypatch.setattr(module, 'CHUNK_SIZE', 2)
    real = module._lookup_flags
    sizes = []

    def lookup(connection, keys):
        sizes.append(len(keys))
        return real(connection, keys)

    monkeypatch.setattr(module, '_lookup_flags', lookup)
    result = application.test_client().get(URL).get_json()
    assert sizes == [2, 2, 2, 2, 1]
    assert result['feature_count'] == 9 and result['matched_count'] == 7


def test_lru_does_not_retain_more_than_32_tile_payloads(setup, monkeypatch):
    data, _ = setup
    for x in range(33):
        write_tile(data, [attrs('S3')], z=6, x=x, y=24)
    application = app(data)
    module = importlib.import_module('comparison_tiles')
    real = module.read_attribute_tile
    decoded = []

    def read(*args, **kwargs):
        decoded.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, 'read_attribute_tile', read)
    client = application.test_client()
    for x in range(33):
        assert client.get(f'/comparison/water/tiles/sa001/6/{x}/24').status_code == 200
    assert client.get('/comparison/water/tiles/sa001/6/32/24').status_code == 200
    assert len(decoded) == 33
    assert client.get('/comparison/water/tiles/sa001/6/0/24').status_code == 200
    assert len(decoded) == 33  # Evicted RAM entries are now reused from disk.


def test_concurrent_decode_capacity_is_bounded(setup, monkeypatch):
    data, _ = setup
    write_tile(data, [attrs('S3')])
    write_tile(data, [attrs('S3')], y=397)
    application = app(data)
    module = importlib.import_module('comparison_tiles')
    real = module.read_attribute_tile
    entered, release = threading.Event(), threading.Event()
    results = []

    def waiting(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, 'read_attribute_tile', waiting)
    monkeypatch.setattr(module, 'BUILD_WAIT_SECONDS', .02)
    thread = threading.Thread(target=lambda: results.append(application.test_client().get(URL).status_code))
    thread.start()
    try:
        assert entered.wait(3)
        response = application.test_client().get('/comparison/water/tiles/sa001/10/873/397')
        assert response.status_code == 503 and response.get_json()['error'] == 'comparison_busy'
    finally:
        release.set()
        thread.join(3)
    assert results == [200]


def test_lookup_and_tiles_can_share_one_verified_package(setup, monkeypatch):
    data, package = setup
    write_tile(data, [attrs('S3')])
    app(data)
    comparison = importlib.import_module('comparison')
    sidecars = importlib.import_module('comparison_tiles')
    shared = comparison.ComparisonPackage(package.directory)
    application = Flask(__name__)
    application.register_blueprint(comparison.create_comparison_blueprint(package.directory, package=shared))
    application.register_blueprint(sidecars.create_comparison_tiles_blueprint(data, package=shared))
    actual_hash = comparison._sha256
    reads = []

    def counted(path):
        reads.append(path)
        return actual_hash(path)

    monkeypatch.setattr(comparison, '_sha256', counted)
    client = application.test_client()
    assert client.get('/comparison/water/metadata').status_code == 200
    assert client.get(URL).status_code == 200
    assert len(reads) == 1
