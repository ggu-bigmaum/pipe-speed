"""Disposable, versioned on-disk cache. The audited comparison DB stays read-only."""
import json
import sqlite3
from contextlib import closing


class TileFlagCache:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('''CREATE TABLE IF NOT EXISTS tile_flags (
            layer TEXT, z INTEGER, x INTEGER, y INTEGER, input_key TEXT NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY(layer,z,x,y)) WITHOUT ROWID''')
        return db

    def get(self, layer, coord, key):
        if not self.path.exists():
            return None
        with closing(self._connect()) as db:
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
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO tile_flags VALUES(?,?,?,?,?,?)',
                       (layer, *coord, json.dumps(key), json.dumps(payload, separators=(',', ':'))))
