"""Original TIFF delivery and grid-ID-based result lookup; no database on the server."""
import json
import csv
import math
import threading
from contextlib import contextmanager
from pathlib import Path
from flask import Blueprint, jsonify, request, send_from_directory, abort

FILES = ('pipe_case1_m.tif', 'pipe_case2_m.tif', 'pipe_dist_result.xlsx')
HEADERS = ('grid_id','case1_m','case2_m','pass_flag','pipe_id','near_x','near_y','near_lon','near_lat')


@contextmanager
def result_rows(path):
    if path.suffix == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            yield iter(csv.reader(handle))
    else:
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            yield iter(workbook['격자별_거리'].values)
        finally:
            workbook.close()


def identifier(value):
    if value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError('정수가 아닌 숫자 ID가 있습니다.')
        return str(int(value))
    return str(value).strip()


class DistanceData:
    def __init__(self, directory, result_filename='pipe_dist_result.xlsx'):
        if result_filename not in ('pipe_dist_result.xlsx', 'pipe_dist_result.csv'):
            raise ValueError('지원하지 않는 result 파일 형식입니다.')
        self.directory = Path(directory)
        self.result_filename = result_filename
        self.files = (*FILES[:2], result_filename)
        self.lock = threading.Lock()
        self.signature = None
        self._grid_geometry = None

    def load(self):
        paths = [self.directory/f for f in (*self.files,'grid_index.json')]
        signature = tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths)
        with self.lock:
            if signature == self.signature:
                return self
            from PIL import Image
            index = json.loads(paths[-1].read_text(encoding='utf-8'))
            if index['crs'] != 'EPSG:32652':
                raise ValueError('격자 대응표의 좌표계는 EPSG:32652여야 합니다.')
            w,h = index['width'],index['height']
            sx,sy=index['pixel_size']; ox,oy=index['origin']
            pixels={}; grid_ids=set()
            for grid_id,col,row in index['cells']:
                if not (0<=col<w and 0<=row<h) or (col,row) in pixels or grid_id in grid_ids:
                    raise ValueError('격자 대응표에 범위 오류 또는 중복 ID가 있습니다.')
                pixels[(col,row)]=grid_id; grid_ids.add(grid_id)
            arrays=[]
            for file in FILES[:2]:
                with Image.open(self.directory/file) as im:
                    keys=im.tag_v2.get(34735,())
                    epsg=next((keys[i+3] for i in range(4,len(keys),4) if keys[i]==3072),None)
                    if im.size!=(w,h) or epsg!=32652 or tuple(im.tag_v2[33550][:2])!=(sx,sy) or tuple(im.tag_v2[33922][3:5])!=(ox,oy):
                        raise ValueError('TIF의 좌표계·격자가 대응표와 다릅니다. 격자 대응표를 확인해 주세요.')
                    if float(im.tag_v2.get(42113,'nan'))!=index['nodata']:
                        raise ValueError('TIF의 NoData 정의가 대응표와 다릅니다.')
                    arrays.append(list(im.get_flattened_data() if hasattr(im,'get_flattened_data') else im.getdata()))
            records={}
            with result_rows(self.directory/self.result_filename) as values:
                columns=next(values, ())
                if not set(HEADERS).issubset(columns):
                    raise ValueError('result에 필요한 열이 없습니다: '+', '.join(set(HEADERS)-set(columns)))
                indices=[columns.index(k) for k in HEADERS]
                for row in values:
                    if not row or all(v in (None, '') for v in row):
                        continue
                    if len(row) <= max(indices):
                        raise ValueError('result에 열이 부족한 행이 있습니다.')
                    record=dict(zip(HEADERS,(row[i] for i in indices)))
                    gid=identifier(record['grid_id']); record['grid_id']=gid
                    record['pipe_id']=identifier(record['pipe_id'])
                    if not gid or gid in records:
                        raise ValueError('result에 비어 있거나 중복된 grid_id가 있습니다.')
                    for key in ('case1_m','case2_m','near_x','near_y','near_lon','near_lat'):
                        if record[key] is None or not math.isfinite(float(record[key])):
                            raise ValueError('result에 유효하지 않은 수치가 있습니다: '+key)
                        record[key]=float(record[key])
                    if record['pass_flag'] not in (0,1,'0','1'):
                        raise ValueError('pass_flag는 0 또는 1이어야 합니다.')
                    record['pass_flag']=int(record['pass_flag'])
                    records[gid]=record
            if set(records)!=grid_ids:
                raise ValueError('result의 격자 ID와 위치 대응표가 다릅니다.')
            for (col,row),gid in pixels.items():
                for i,field in enumerate(('case1_m','case2_m')):
                    if not math.isclose(arrays[i][row*w+col],records[gid][field],rel_tol=2e-6,abs_tol=.001):
                        raise ValueError('TIF와 result가 같은 버전인지 확인해 주세요. grid_id='+gid)
            if any(sum(v!=index['nodata'] for v in a)!=len(records) for a in arrays):
                raise ValueError('TIF의 유효 셀과 result 수가 다릅니다.')
            self.index,self.pixels,self.records=index,pixels,records
            self._grid_geometry=None
            self.signature=signature
            return self

    def gridlines(self):
        self.load()
        with self.lock:
            if self._grid_geometry is None:
                horizontal,vertical={},{}
                for col,row in self.pixels:
                    horizontal.setdefault(row,set()).add(col)
                    horizontal.setdefault(row+1,set()).add(col)
                    vertical.setdefault(col,set()).add(row)
                    vertical.setdefault(col+1,set()).add(row)
                ox,oy=self.index['origin']; sx,sy=self.index['pixel_size']
                lines=[]
                for axis,edges in (('h',horizontal),('v',vertical)):
                    for fixed,positions in sorted(edges.items()):
                        ordered=sorted(positions); start=previous=ordered[0]
                        runs=[]
                        for position in ordered[1:]:
                            if position!=previous+1:
                                runs.append((start,previous+1)); start=position
                            previous=position
                        runs.append((start,previous+1))
                        for start,end in runs:
                            if axis=='h':
                                lines.append([[ox+start*sx,oy-fixed*sy],[ox+end*sx,oy-fixed*sy]])
                            else:
                                lines.append([[ox+fixed*sx,oy-start*sy],[ox+fixed*sx,oy-end*sy]])
                # Compact source-CRS runs. The client adds cell vertices before
                # reprojection so long runs follow the actual raster grid.
                self._grid_geometry={'type':'MultiLineString','coordinates':lines}
            return {'crs':self.index['crs'],'pixel_size':self.index['pixel_size'],'geometry':self._grid_geometry}


def create_distance_blueprint(directory, *, name='distance', url_prefix='/distance',
                              dataset_id='kict_water_seoul', label='건기연 상수(서울)',
                              result_filename='pipe_dist_result.xlsx'):
    bp=Blueprint(name,__name__,url_prefix=url_prefix)
    store=DistanceData(directory,result_filename)

    @bp.errorhandler(FileNotFoundError)
    @bp.errorhandler(OSError)
    @bp.errorhandler(ValueError)
    @bp.errorhandler(KeyError)
    @bp.errorhandler(ImportError)
    def data_error(exc):
        message='거리 자료를 읽지 못했습니다. 입력 파일과 requirements-server.txt 설치 상태를 확인해 주세요.'
        if isinstance(exc,(ValueError,KeyError)):
            message=str(exc)
        return jsonify(error=message),503

    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control']='no-store'
        return response

    @bp.get('/meta')
    def meta():
        data=store.load(); result={k:v for k,v in data.index.items() if k not in ('cells','columns')}
        result.update(dataset_id=dataset_id,label=label,result_filename=result_filename,result_count=len(data.records),
            cases=[{'id':f'case{i+1}','filename':f,'bytes':(store.directory/f).stat().st_size,
                    'label':label} for i,(f,label) in enumerate(zip(FILES[:2],('Case 1 · 중심점부터 관로까지','Case 2 · 관로 통과 격자는 0')))])
        return jsonify(result)

    @bp.get('/files/<path:filename>')
    def original_file(filename):
        if filename not in store.files:
            abort(404)
        return send_from_directory(store.directory,filename,conditional=False,max_age=0)

    @bp.get('/gridlines')
    def gridlines():
        return jsonify(store.gridlines())

    @bp.get('/cell')
    def cell():
        data=store.load()
        try:
            col=int(request.args['col']); row=int(request.args['row'])
        except (KeyError,ValueError):
            return jsonify(error='정수 col과 row가 필요합니다.'),400
        if not (0<=col<data.index['width'] and 0<=row<data.index['height']):
            return jsonify(error='자료 범위 밖입니다.'),400
        gid=data.pixels.get((col,row))
        if gid is None:
            return jsonify(matched=False,reason='nodata')
        sx,sy=data.index['pixel_size']; ox,oy=data.index['origin']
        return jsonify(matched=True,dataset_id=dataset_id,col=col,row=row,center=[ox+(col+.5)*sx,oy-(row+.5)*sy],**data.records[gid])

    return bp
