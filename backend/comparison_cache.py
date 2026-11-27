"""Disposable, versioned on-disk cache. The audited comparison DB stays read-only."""
import base64
import binascii
import json
import sqlite3
import threading
from contextlib import contextmanager

CONTENT_FORMAT = 'tile-content-v1'


def content_key(pair, layer, coord, version, sha, peer_sha):
    rule = 'single-display-peer-v2' if layer.startswith('kict_') else 'single-display-v1'
    return (CONTENT_FORMAT, rule, version, pair, layer, *coord, sha, peer_sha)


def valid_payload(payload, key):
    """Validate identity and bitset structure; source content is hashed by caller."""
    if not isinstance(payload, dict):
        return False
    if (payload.get('available') is not True or type(payload.get('schema_version')) is not int
            or payload['schema_version'] != 1 or payload.get('version') != key[2]
            or payload.get('layer') != key[4] or payload.get('sha256') != key[8]
            or payload.get('counterpart_sha256') != key[9]):
        return False
    count, matched = payload.get('feature_count'), payload.get('matched_count')
    if type(count) is not int or type(matched) is not int or not 0 <= matched <= count <= 4_000_000:
        return False
    encoded = payload.get('bits_base64')
    if not isinstance(encoded, str) or len(encoded) > 666668:
        return False
    try:
        bits = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return False
    if len(bits) != (count + 7) // 8 or (count % 8 and bits[-1] >> (count % 8)):
        return False
    return sum(bin(byte).count('1') for byte in bits) == matched


class TileFlagCache:
    def __init__(self, path):
        self.path = path
        self._initialized = False
        self._lock = threading.Lock()
        self.keep_open = False  # Offline precomputation only; live requests close.
        self._local = threading.local()

    @contextmanager
    def _session(self):
        if self.keep_open:
            if not hasattr(self._local, 'db'):
                self._local.db = self._connect()
            yield self._local.db
        else:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        with self._lock:
            if not self._initialized:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('''CREATE TABLE IF NOT EXISTS tile_flags (
                    layer TEXT, z INTEGER, x INTEGER, y INTEGER, input_key TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(layer,z,x,y)) WITHOUT ROWID''')
                db.execute('CREATE TABLE IF NOT EXISTS cache_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('INSERT OR REPLACE INTO cache_metadata VALUES (?,?)',
                           ('content_key_format', CONTENT_FORMAT))
                db.commit()
                self._initialized = True
        # This is a disposable derived cache, not an authoritative database.
        db.execute('PRAGMA synchronous=NORMAL')
        return db

    def get(self, layer, coord, key):
        if not self.path.exists():
            return None
        with self._session() as db:
            row = db.execute('SELECT payload FROM tile_flags WHERE layer=? AND z=? AND x=? AND y=? AND input_key=?',
                             (layer, *coord, json.dumps(key))).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            return value if isinstance(value, dict) and value.get('available') is True else None
        except (ValueError, TypeError):
            return None

    def put(self, layer, coord, key, payload):
        with self._session() as db, db:
            db.execute('INSERT OR REPLACE INTO tile_flags VALUES(?,?,?,?,?,?)',
                       (layer, *coord, json.dumps(key), json.dumps(payload, separators=(',', ':'))))

    def get_verified(self, layer, coord, key):
        """Return (payload, needs_migration); never trust legacy paths as identity."""
        if not self.path.exists():
            return None, False
        with self._session() as db:
            row = db.execute('SELECT input_key,payload FROM tile_flags WHERE layer=? AND z=? AND x=? AND y=?',
                             (layer, *coord)).fetchone()
        if row is None:
            return None, False
        try:
            stored, payload = json.loads(row[0]), json.loads(row[1])
        except (ValueError, TypeError):
            return None, False
        current = stored == list(key)
        # Only the exact historical display rule and package are transferable.
        legacy = (isinstance(stored, list) and len(stored) == (6 if key[1] == 'single-display-peer-v2' else 5)
                  and stored[:2] == [key[1], key[2]])
        if not (current or legacy) or not valid_payload(payload, key):
            return None, False
        return payload, not current
