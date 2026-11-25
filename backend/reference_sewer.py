"""Read-only display of recorded non-Seoul correction links, separate from originals."""
import hashlib
import json
from pathlib import Path
from flask import Blueprint, jsonify, send_file


def create_reference_sewer_blueprint(directory):
    root = Path(directory)
    bp = Blueprint('reference_sewer', __name__, url_prefix='/reference/sewer')
    cached = {}

    def verified():
        data = root / 'connections.geojson'
        manifest = root / 'manifest.json'
        key = (data.stat().st_mtime_ns, data.stat().st_size, manifest.stat().st_mtime_ns)
        if cached.get('key') != key:
            info = json.loads(manifest.read_text(encoding='utf8'))
            if info.get('status') != 'complete' or info.get('coordinate_source') != 'recorded-wkt':
                raise ValueError('Unverified reference')
            if hashlib.sha256(data.read_bytes()).hexdigest() != info.get('sha256'):
                raise ValueError('Reference changed')
            cached.update(key=key, info=info)
        return data, cached['info']

    @bp.errorhandler(OSError)
    @bp.errorhandler(ValueError)
    def missing(error):
        return jsonify(error='참고용 보정 자료 확인 필요'), 503

    @bp.get('/metadata')
    def metadata():
        _, info = verified()
        return jsonify(reference_only=True, count=info['count'], regions=info.get('regions', []))

    @bp.get('/connections.geojson')
    def connections():
        data, _ = verified()
        return send_file(data, mimetype='application/geo+json', conditional=True)

    return bp
