"""Business decisions over previously validated geometric evidence.

No geometry is edited here. Candidate absence and a merely equal identifier
never prove source-only existence. The strict display flag is independent of
the six business states and comes only from full-original GEOS equality.
"""

STATES = ('MATCH', 'PARTIAL_MATCH', 'KICT_ONLY', 'LOCAL_ONLY', 'CONFLICT', 'NOT_COMPARED')
VERSION = 'seoul-water-evidence-v1-20261125'
FULL_RATIO = .95

REASONS = {
    'reference_unavailable': '해당 지역의 지자체 비교 자료가 없습니다.',
    'whole_source_equal': '두 원본 관로의 전체 위치와 형상이 같습니다.',
    'source_mapping_unverified': '기존 비교 결과와 현재 원본의 대응을 확정하지 못했습니다.',
    'boundary_scope_difference': '이전 비교는 서울 경계로 잘린 구간을 기준으로 하여 전체 원본 판정을 보류했습니다.',
    'regression_review': '방향·연속 길이 보완 후 대응 비율이 감소한 검토 대상입니다.',
    'competing_correspondence': '같은 구간에 여러 상대 관로가 중복 대응하여 자동 확정을 보류했습니다.',
    'sustained_full': '방향·연속 길이 조건을 통과한 구간이 관로 길이의 95% 이상입니다.',
    'sustained_partial': '방향·연속 길이 조건을 통과한 일부 구간이 확인됩니다.',
    'absence_unconfirmed': '5m 이내 대응 후보가 없지만 한 자료에만 존재하는지는 추가 확인이 필요합니다.',
    'same_id_different_path': '같은 ID의 관로가 있으나 인정된 연속 대응 구간이 없습니다.',
    'no_sustained_path': '가까운 관로는 있으나 인정된 연속 대응 구간이 없습니다.',
}


def classify(e):
    strict = bool(e.get('whole_geometry_equal') and e.get('same_id_geometry_equal'))
    result = dict(status='CONFLICT', candidate_status=None,
                  exact_same_id_geometry=strict, rule_version=VERSION)

    def decision(state, reason):
        return dict(result, status=state, reason_code=reason, reason=REASONS[reason])

    if not e.get('reference_available', True):
        return decision('NOT_COMPARED', 'reference_unavailable')
    if e.get('whole_geometry_equal'):
        return decision('MATCH', 'whole_source_equal')
    if not e.get('source_verified'):
        return decision('CONFLICT', 'source_mapping_unverified')
    if not e.get('fully_inside'):
        return decision('CONFLICT', 'boundary_scope_difference')
    if e.get('regression_review_required'):
        return decision('CONFLICT', 'regression_review')
    if (e.get('competing_overlap_m') or 0) > max(.05, (e.get('length_m') or 0) * .05):
        return decision('CONFLICT', 'competing_correspondence')
    coverage = e.get('coverage_ratio') or 0
    if e.get('supporting_count', 0) and coverage > 0:
        if coverage >= FULL_RATIO:
            return decision('MATCH', 'sustained_full')
        return decision('PARTIAL_MATCH', 'sustained_partial')
    previous = e.get('previous_status')
    if previous == 'no_candidate_within_5m':
        result['candidate_status'] = 'KICT_ONLY' if e['dataset'] == 'kict' else 'LOCAL_ONLY'
        return decision('CONFLICT', 'absence_unconfirmed')
    if previous == 'same_id_geometry_changed':
        return decision('CONFLICT', 'same_id_different_path')
    return decision('CONFLICT', 'no_sustained_path')
