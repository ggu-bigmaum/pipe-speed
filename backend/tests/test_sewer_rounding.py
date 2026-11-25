import importlib.util
from pathlib import Path
import pytest
shapely=pytest.importorskip('shapely')
spec=importlib.util.spec_from_file_location('sewer_builder',Path(__file__).resolve().parents[2]/'tools/comparison/build_sewer.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_whole_equal_reversed_multipart_duplicates_and_crossing():
    a=shapely.from_wkt(['LINESTRING(0 0,1 1)','LINESTRING(0 1,1 0)','LINESTRING(0 0,.5 .5)'])
    b=shapely.from_wkt(['MULTILINESTRING((1.0001 1.0001,.5001 .5001),(.5001 .5001,.0001 .0001))','LINESTRING(0 0,1 1)'])
    i,j,_,_=m.matched_pairs(a,b)
    assert set(zip(i,j))=={(0,0),(0,1)}

def test_collapsed_requires_review_and_inputs_unchanged():
    a=shapely.from_wkt(['LINESTRING(1.00001 2.00001,1.00002 2.00002)'])
    before=shapely.to_wkb(a).tolist()
    i,j,av,bv=m.matched_pairs(a,a)
    assert len(i)==0 and not av[0] and not bv[0]
    assert shapely.to_wkb(a).tolist()==before

def test_round_boundary_is_not_distance_tolerance():
    a=shapely.from_wkt(['LINESTRING(100.12349 0,101.12349 1)'])
    b=shapely.from_wkt(['LINESTRING(100.12351 0,101.12351 1)'])
    assert len(m.matched_pairs(a,b)[0])==0
