from pathlib import Path
import sys
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_missing_sewer_package_is_unavailable_not_zero_matches(tmp_path):
    from sewer_comparison import create_sewer_comparison_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_sewer_comparison_blueprint(tmp_path))
    response = app.test_client().get('/comparison/sewer/metadata')
    assert response.status_code == 503
    value = response.get_json()
    assert value['available'] is False and value['error'] == 'comparison_unavailable'
    assert '확인할 수 없' in value['message']
    assert 'counts' not in value and 'version' not in value
    assert 'no-store' in response.headers['Cache-Control']
