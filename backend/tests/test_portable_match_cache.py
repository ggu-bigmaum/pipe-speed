"""Portable flags must depend on MVT content, never deployment paths/timestamps."""
import gzip
import hashlib
import io
import json
import os
import sqlite3
import threading

import pytest
from test_comparison_tiles import setup, write_tile, attrs, app, URL
from comparison import ComparisonPackage
from comparison_tiles import ComparisonTileSidecars
from test_overlap_cache import kict_water_tile


def test_streaming_hash_is_compression_independent_and_bounded():
    from comparison_mvt import hash_tile_stream
    raw=b'content'*400000
    class BoundedReader(io.BytesIO):
        def read(self,size=-1):
            assert 0 <= size <= 1024*1024
            return super().read(size)
    expected=hashlib.sha256(raw).hexdigest()
    assert hash_tile_stream(BoundedReader(raw))==expected
    assert hash_tile_stream(BoundedReader(gzip.compress(raw)))==expected
    with pytest.raises(ValueError): hash_tile_stream(io.BytesIO(gzip.compress(raw)),100)


def test_hashing_has_a_shared_bound_without_taking_build_slot(setup,monkeypatch):
    data,package=setup
    path,_=write_tile(data,[attrs('S3')])
    services=[ComparisonTileSidecars(data,ComparisonPackage(package.directory)) for _ in range(3)]
    entered,release=threading.Event(),threading.Event()
    lock=threading.Lock(); active=0;peak=0;results=[]
    def slow_hash(*args):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active)
            if active==2:entered.set()
        assert release.wait(3)
        with lock:active-=1
        return 'hash'
    monkeypatch.setattr('comparison_tiles.hash_tile_stream',slow_hash)
    threads=[threading.Thread(target=lambda s=s:results.append(s._content_sha(path,(10,873,396)))) for s in services]
    for thread in threads:thread.start()
    try:
        assert entered.wait(3)
        with lock:assert active==2
    finally:
        release.set()
        for thread in threads:thread.join(3)
    assert results==['hash']*3 and peak==2


def forbid_build(service, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Existing verified content must not rebuild flags')
    monkeypatch.setattr(service, '_build', forbidden)


def legacy_row(service, coord, payload, *, old_rule=False):
    key = ['single-display-v1', payload['version'], r'tiles_full\sa001\old.pbf', 123, 987]
    if service.dataset == 'kict':
        if not old_rule:
            key[0] = 'single-display-peer-v2'
            key.append([r'tiles_full\sa001\peer.pbf', 456, 654])
    service.disk.put(service.layer, coord, key, payload)


def test_content_hit_survives_new_directory_and_compression(setup, monkeypatch):
    data, package = setup
    path, raw = write_tile(data, [attrs('S3')])
    first = ComparisonTileSidecars(data, ComparisonPackage(package.directory)).get(10,873,396)
    # Same uncompressed MVT in a different fallback directory, with new times.
    moved = data/'tiles_low/sa001/10/873/396.pbf'
    moved.parent.mkdir(parents=True)
    moved.write_bytes(raw)
    path.unlink(); path.parent.rmdir(); path.parent.parent.rmdir()
    restarted = ComparisonTileSidecars(data, ComparisonPackage(package.directory))
    forbid_build(restarted, monkeypatch)
    assert restarted.get(10,873,396) == first


def test_windows_legacy_key_migrates_only_after_content_proof(setup, monkeypatch):
    data, package = setup
    path, _ = write_tile(data, [attrs('S3')])
    service = ComparisonTileSidecars(data, ComparisonPackage(package.directory))
    first = service.get(10,873,396)
    legacy_row(service, (10,873,396), first)
    os.utime(path, ns=(123456789000,123456789000))
    restarted = ComparisonTileSidecars(data, ComparisonPackage(package.directory))
    forbid_build(restarted, monkeypatch)
    assert restarted.get(10,873,396) == first
    with sqlite3.connect(service.disk.path) as db:
        key = json.loads(db.execute('SELECT input_key FROM tile_flags').fetchone()[0])
    assert key[0] == 'tile-content-v1' and first['sha256'] in key
    assert not any('\\' in str(part) or '.pbf' in str(part) for part in key)


def test_pbf_to_mbtiles_keeps_same_flags_and_peer_proof(setup, monkeypatch):
    data, package = setup
    path = data/'tiles_national/kict_national/11/1746/793.pbf'
    path.parent.mkdir(parents=True)
    raw = kict_water_tile([100,7,9]); path.write_bytes(raw)
    write_tile(data,[attrs('S3')],z=11,x=1746,y=793)
    first_service = ComparisonTileSidecars(data,ComparisonPackage(package.directory),layer='kict_national')
    first = first_service.get(11,1746,793)
    legacy_row(first_service,(11,1746,793),first)
    with sqlite3.connect(data/'kict_national.mbtiles') as db:
        db.execute('CREATE TABLE tiles(zoom_level,tile_column,tile_row,tile_data)')
        db.execute('INSERT INTO tiles VALUES(11,1746,793,?)',(gzip.compress(raw),))
    restarted = ComparisonTileSidecars(data,ComparisonPackage(package.directory),layer='kict_national')
    forbid_build(restarted, monkeypatch)
    assert restarted.get(11,1746,793) == first


def test_disk_hit_does_not_wait_for_unrelated_build(setup, monkeypatch):
    data, package = setup
    write_tile(data,[attrs('S3')])
    first = ComparisonTileSidecars(data,ComparisonPackage(package.directory)).get(10,873,396)
    restarted = ComparisonTileSidecars(data,ComparisonPackage(package.directory))
    monkeypatch.setattr('comparison_tiles.BUILD_WAIT_SECONDS',0)
    restarted._build_slot.acquire()
    try:
        assert restarted.get(10,873,396) == first
    finally:
        restarted._build_slot.release()


@pytest.mark.parametrize('change', ['sha', 'version', 'bits', 'count', 'rule'])
def test_invalid_legacy_payload_cannot_skip_rebuild(setup, monkeypatch, change):
    data, package = setup
    write_tile(data,[attrs('S3')])
    service = ComparisonTileSidecars(data,ComparisonPackage(package.directory))
    first = service.get(10,873,396)
    bad = dict(first)
    if change == 'sha': bad['sha256'] = '0'*64
    if change == 'version': bad['version'] = 'unrelated-package'
    if change == 'bits': bad['bits_base64'] = '/w=='
    if change == 'count': bad['matched_count'] = 0
    legacy_row(service,(10,873,396),bad)
    if change == 'rule':
        with sqlite3.connect(service.disk.path) as db:
            db.execute("UPDATE tile_flags SET input_key=?",(json.dumps(['unknown-rule',first['version']]),))
    restarted = ComparisonTileSidecars(data,ComparisonPackage(package.directory))
    calls = []
    real = restarted._build
    def build(*args,**kwargs):
        calls.append(True); return real(*args,**kwargs)
    monkeypatch.setattr(restarted,'_build',build)
    assert restarted.get(10,873,396) == first and calls == [True]


def test_conditional_http_revalidates_and_changes_etag_with_content(setup):
    data, _ = setup
    write_tile(data,[attrs('S3')])
    client = app(data).test_client()
    first = client.get(URL)
    assert first.headers.get('ETag')
    assert 'must-revalidate' in first.headers['Cache-Control']
    second = client.get(URL, headers={'If-None-Match':first.headers['ETag']})
    assert second.status_code == 304 and not second.data
    write_tile(data,[attrs('S2')])
    third = client.get(URL, headers={'If-None-Match':first.headers['ETag']})
    assert third.status_code == 200 and third.headers['ETag'] != first.headers['ETag']
    assert client.get(URL+'bad').headers['Cache-Control'] == 'no-store'


def test_same_size_same_mtime_replacement_invalidates_memory(setup):
    data, package = setup
    path, raw = write_tile(data,[attrs('S3')])
    path.write_bytes(raw)
    service = ComparisonTileSidecars(data,ComparisonPackage(package.directory))
    first = service.get(10,873,396)
    stat = path.stat()
    from test_comparison_tiles import tile
    replacement = path.with_suffix('.new')
    replacement.write_bytes(tile([attrs('S2')]))
    os.utime(replacement,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    replacement.replace(path)
    assert path.stat().st_size == stat.st_size
    second = service.get(10,873,396)
    assert second['sha256'] != first['sha256'] and second['matched_count'] == 0


@pytest.mark.parametrize('change', ['peer', 'missing_peer', 'old_rule', 'package'])
def test_legacy_kict_does_not_reuse_wrong_counterpart_or_rule(setup,monkeypatch,change):
    data, package = setup
    path = data/'tiles_national/kict_national/11/1746/793.pbf'
    path.parent.mkdir(parents=True); path.write_bytes(kict_water_tile([9]))
    peer,_ = write_tile(data,[attrs('S3')],z=11,x=1746,y=793)
    service=ComparisonTileSidecars(data,ComparisonPackage(package.directory),layer='kict_national')
    first=service.get(11,1746,793)
    legacy_row(service,(11,1746,793),first,old_rule=change=='old_rule')
    if change=='peer': write_tile(data,[attrs('S2')],z=11,x=1746,y=793)
    if change=='missing_peer': peer.unlink()
    if change=='package':
        with sqlite3.connect(package.path) as db:
            db.execute("UPDATE tile_groups SET all_strict=0 WHERE dataset='seoul'")
        package.finish()
    restarted=ComparisonTileSidecars(data,ComparisonPackage(package.directory),layer='kict_national')
    real=restarted._build; calls=[]
    def build(*args,**kwargs): calls.append(True); return real(*args,**kwargs)
    monkeypatch.setattr(restarted,'_build',build)
    result=restarted.get(11,1746,793)
    assert calls==[True]
    if change=='missing_peer': assert result['counterpart_sha256'] is None


def test_sewer_legacy_keys_are_portable_on_both_sides(tmp_path,monkeypatch):
    from test_sewer_sidecar import StrictPackage,sewer_tile
    from comparison_mvt import _fields
    from test_comparison_mvt import field
    package=StrictPackage(tmp_path/'rules.sqlite')
    with sqlite3.connect(package.path) as db:
        db.execute('INSERT INTO tile_groups VALUES(?,?,?)',('kict','["1"]',1))
        db.execute('CREATE TABLE pairs(kict_id,seoul_id)')
        db.execute('INSERT INTO pairs VALUES(1,1)')
    for layer in ['sb001','kict_sewer_seoul']:
        raw=sewer_tile([1])
        if layer.startswith('kict'):
            body=bytes(next(_fields(memoryview(raw)))[2])
            raw=field(3,body.replace(field(1,b'sb001'),field(1,layer.encode())))
        path=tmp_path/'tiles_full'/layer/'12/3492/1586.pbf'
        path.parent.mkdir(parents=True);path.write_bytes(raw)
    for layer in ['sb001','kict_sewer_seoul']:
        first_service=ComparisonTileSidecars(tmp_path,package,comparison='sewer',layer=layer)
        first=first_service.get(12,3492,1586)
        legacy_row(first_service,(12,3492,1586),first)
        restarted=ComparisonTileSidecars(tmp_path,package,comparison='sewer',layer=layer)
        forbid_build(restarted,monkeypatch)
        assert restarted.get(12,3492,1586)==first
