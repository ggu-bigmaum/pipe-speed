from pathlib import Path
import sys
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_unapproved_sewer_criterion_is_explicitly_pending_not_zero_matches():
    from sewer_comparison import create_sewer_comparison_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_sewer_comparison_blueprint())
    response = app.test_client().get('/comparison/sewer/metadata')
    assert response.status_code == 503
    value = response.get_json()
    assert value['available'] is False and value['error'] == 'criteria_pending'
    assert '검토 중' in value['message']
    assert 'counts' not in value and 'version' not in value
    assert 'no-store' in response.headers['Cache-Control']
