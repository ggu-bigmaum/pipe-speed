import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sewer_comparison import SewerPackage, RULE


@pytest.fixture
def package(tmp_path):
    root = tmp_path / 'comparison' / 'sewer'
    root.mkdir(parents=True)
    db = root / 'sewer_comparison.sqlite'
    counts = {'pairs': 0}
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE metadata(key,value)')
        connection.executemany('INSERT INTO metadata VALUES(?,?)', [
            ('rule_version', json.dumps(RULE)), ('counts', json.dumps(counts))])
    sources = []
    for layer in ('kict_sewer_seoul', 'sb001'):
        marker = tmp_path / 'tiles_full' / layer / 'conversion_complete.json'
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({'source_sha256': layer}), encoding='utf-8')
        sources.append({'layer': 'seoul_sewer' if layer == 'sb001' else layer, 'sha256': layer})
    manifest = dict(status='complete', schema_version=1, rule_version=RULE, counts=counts,
                    sources=sources, output=dict(bytes=db.stat().st_size,
                    sha256=hashlib.sha256(db.read_bytes()).hexdigest()))
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return SewerPackage(root)


def test_verification_is_cached_but_tile_source_change_invalidates(package):
    assert package.verified()['available']
    assert package.verified() is package._cached[1]
    marker = package.root.parent.parent / 'tiles_full/sb001/conversion_complete.json'
    marker.write_text('{"source_sha256":"different-source"}', encoding='utf-8')
    with pytest.raises(ValueError, match='Tile source mismatch'):
        package.verified()


def test_modified_database_is_rejected(package):
    package.verified()
    with package.path.open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='Package hash mismatch'):
        package.verified()


def test_unapproved_rule_is_rejected(package):
    manifest = json.loads(package.manifest_path.read_text(encoding='utf-8'))
    manifest['rule_version'] = 'unapproved'
    package.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid comparison manifest'):
        package.verified()
