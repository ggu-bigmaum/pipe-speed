"""Read-only per-object sewer evidence, separate from six business states."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
from flask import Blueprint, jsonify, request
from comparison import _sha256
from comparison_keys import sewer_tile_key

RULE='sewer-whole-round3-half-up-v1'
LABEL='3자리 반올림 후 형상 일치'
DESCRIPTION='ID 조건 없이 비교용 좌표를 소수점 3자리로 반올림한 뒤 전체 형상을 비교합니다.'
LAYERS={'kict_sewer_seoul':'kict','sb001':'seoul'}

class SewerPackage:
    def __init__(self,directory):
        self.root=Path(directory).resolve();self.path=self.root/'sewer_comparison.sqlite'
        self.manifest_path=self.root/'manifest.json';self._cached=None;self._lock=threading.Lock()

    def connect(self):
        c=sqlite3.connect(self.path.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
        return closing(c)

    def verified(self):
        with self._lock:
            data=self.root.parent.parent
            markers=[data/'tiles_full'/layer/'conversion_complete.json' for layer in LAYERS]
            paths=[self.path,self.manifest_path]+markers
            revision=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths)
            if self._cached and self._cached[0]==revision:return self._cached[1]
            if revision[1][0]>1024*1024:raise ValueError('Manifest too large')
            m=json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if m.get('status')!='complete' or m.get('schema_version')!=1 or m.get('rule_version')!=RULE:raise ValueError('Invalid comparison manifest')
            if m['output']['bytes']!=revision[0][0] or _sha256(self.path)!=m['output']['sha256']:raise ValueError('Package hash mismatch')
            expected={('sb001' if x['layer']=='seoul_sewer' else x['layer']):x['sha256'] for x in m['sources']}
            for layer,marker in zip(LAYERS,markers):
                if json.loads(marker.read_text(encoding='utf-8'))['source_sha256']!=expected[layer]:raise ValueError('Tile source mismatch')
            with self.connect() as c:
                stored={r['key']:json.loads(r['value']) for r in c.execute('SELECT key,value FROM metadata')}
                if stored.get('rule_version')!=RULE or stored.get('counts')!=m['counts']:raise ValueError('Metadata mismatch')
            info=dict(available=True,schema_version=1,version=RULE+':'+m['output']['sha256'],counts=m['counts'],highlight_label=LABEL,description=DESCRIPTION,rule_version=RULE,layers=list(LAYERS))
            if revision!=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths):raise ValueError('Inputs changed')
            self._cached=revision,info
            return info

def create_sewer_comparison_blueprint(directory=None, *, package=None):
    package=package or SewerPackage(directory or Path(__file__).parent/'data/comparison/sewer')
    bp=Blueprint('sewer_comparison',__name__,url_prefix='/comparison/sewer')
    @bp.errorhandler(OSError)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(TypeError)
    @bp.errorhandler(sqlite3.Error)
    def unavailable(error):
        return jsonify(available=False,error='comparison_unavailable',message='하수 비교 파일을 확인할 수 없습니다.'),503
    @bp.after_request
    def cache(response):
        response.headers['Cache-Control']='no-store';return response
    @bp.get('/metadata')
    def metadata():return jsonify(package.verified())
    @bp.post('/lookup')
    def lookup():
        raw=request.stream.read(8193)
        if len(raw)>8192 or not request.is_json:return jsonify(available=False,error='invalid_request'),400
        try:
            body=json.loads(raw)
            if not isinstance(body,dict) or not {'layer','properties'}.issubset(body) or set(body)-{'layer','properties','source_fid','offset'}:raise ValueError()
            layer=body.get('layer')
            if not isinstance(layer,str) or layer not in LAYERS:return jsonify(available=False,error='unsupported_layer'),404
            props=body.get('properties')
            if not isinstance(props,dict) or set(props)!={'id'}:raise ValueError()
            object_id=int(json.loads(sewer_tile_key(LAYERS[layer],props))[0])
            offset=body.get('offset',0)
            if type(offset) is not int or not 0<=offset<=1000000:raise ValueError()
            if 'source_fid' in body and (type(body['source_fid']) is not int or body['source_fid']!=object_id):raise ValueError()
        except (ValueError,TypeError,KeyError):return jsonify(available=False,error='invalid_request'),400
        info=package.verified();dataset=LAYERS[layer];other='seoul' if dataset=='kict' else 'kict'
        mine='kict_id' if dataset=='kict' else 'seoul_id';theirs='seoul_id' if dataset=='kict' else 'kict_id'
        with package.connect() as c:
            r=c.execute('SELECT * FROM pipes WHERE dataset=? AND id=?',(dataset,object_id)).fetchone()
            if r is None:return jsonify(available=False,error='mapping_not_found',message='현재 타일에 대응하는 관로가 없습니다.'),404
            total=c.execute(f'SELECT COUNT(*) FROM pairs WHERE {mine}=?',(object_id,)).fetchone()[0]
            rows=c.execute(f'SELECT p.* FROM pairs r JOIN pipes p ON p.dataset=? AND p.id=r.{theirs} WHERE r.{mine}=? ORDER BY p.id LIMIT 20 OFFSET ?',(other,object_id,offset)).fetchall()
        flag=None if r['rounded_geometry_match'] is None else bool(r['rounded_geometry_match'])
        pipe=dict(source_fid=object_id,original_id=r['original_id'],feature_role=r['role'],comparison_status=None,rounded_geometry_match=flag,precision_review=bool(r['precision_review']),reason_code=RULE,reason=DESCRIPTION)
        items=[dict(source_fid=x['id'],original_id=x['original_id'],relationship='rounded_geometry',accepted=True,reason=DESCRIPTION)for x in rows]
        return jsonify(available=True,version=info['version'],mapping=dict(ambiguous=False,member_count=1,members=[dict(source_fid=object_id,original_id=r['original_id'])]),pipe=pipe,counterparts=dict(items=items,total=total,accepted=total,offset=offset,next_offset=offset+len(items) if offset+len(items)<total else None))
    return bp
