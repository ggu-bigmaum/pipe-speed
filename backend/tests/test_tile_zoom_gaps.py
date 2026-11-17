"""Existing low-zoom files must not expose an empty middle zoom range."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as server


@pytest.mark.parametrize('zooms,expected_min', [
    (list(range(7)) + list(range(10, 18)), 10),
    (list(range(10, 18)), 10),
    (list(range(18)), 0),
])
def test_seoul_metadata_advertises_a_continuous_zoom_range(tmp_path, monkeypatch, zooms, expected_min):
    for zoom in zooms:
        (tmp_path / 'full' / 'sa001' / str(zoom)).mkdir(parents=True)
    monkeypatch.setattr(server, 'TILES_FULL', tmp_path / 'full')
    monkeypatch.setattr(server, 'TILES', tmp_path / 'legacy')
    monkeypatch.setattr(server, 'TILES_LOW', tmp_path / 'low')
    response = server.app.test_client().get('/tiles/sa001/meta.json')
    assert response.status_code == 200
    metadata = response.get_json()
    assert metadata['minzoom'] == expected_min
    assert metadata['maxzoom'] == 17
    assert all(zoom in zooms for zoom in range(metadata['minzoom'], metadata['maxzoom'] + 1))
    assert metadata['full_zooms'] == zooms
