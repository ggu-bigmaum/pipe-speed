"""Report the explicitly pending sewer criterion without inventing matches."""
from flask import Blueprint, jsonify


def create_sewer_comparison_blueprint():
    bp = Blueprint('sewer_comparison', __name__, url_prefix='/comparison/sewer')

    @bp.get('/metadata')
    def metadata():
        response = jsonify(available=False, error='criteria_pending',
                           message='하수 형상 비교 기준을 검토 중입니다.')
        response.headers['Cache-Control'] = 'no-store'
        return response, 503

    return bp
