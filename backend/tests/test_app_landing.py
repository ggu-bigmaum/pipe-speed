"""The normal app entry point must serve the actual layer-map screen."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import app


@pytest.mark.parametrize('path', ['/app/', '/app/index.html'])
def test_main_entry_renders_layer_map_without_redirect(path):
    response = app.test_client().get(path, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for marker in ('id="layerSidebar"', 'id="sewerCase1"', 'id="sewerCase2"',
                   'id="speedPanel"', 'id="legendPanel"', 'src="distance.js"'):
        assert marker in html
    assert 'http-equiv="refresh"' not in html
    assert 'location.replace' not in html
    assert 'href="benchmark.html"' in html


def test_legacy_benchmark_is_available_separately():
    response = app.test_client().get('/app/benchmark.html')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="genScn"' in html
    assert 'href="./"' in html


def test_old_layer_url_serves_same_main_screen():
    client = app.test_client()
    assert client.get('/app/distance.html').data == client.get('/app/').data
