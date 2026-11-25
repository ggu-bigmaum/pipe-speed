import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.comparison import rules


def evidence(**changes):
    return dict(dict(dataset='kict', reference_available=True,
        source_verified=True, whole_geometry_equal=False,
        same_id_geometry_equal=False, fully_inside=True,
        regression_review_required=False, coverage_ratio=0,
        length_m=10, competing_overlap_m=0, supporting_count=0,
        previous_status='nearby_no_overlap_at_1m'), **changes)


def test_missing_exact_equality_is_not_absence():
    r = rules.classify(evidence(previous_status='no_candidate_within_5m'))
    assert r['status'] == 'CONFLICT'
    assert r['candidate_status'] == 'KICT_ONLY'
    assert not r['exact_same_id_geometry']


def test_no_municipal_source_is_not_compared():
    assert rules.classify(evidence(reference_available=False))['status'] == 'NOT_COMPARED'


def test_exact_geometry_different_id_matches_but_is_not_highlighted():
    r = rules.classify(evidence(whole_geometry_equal=True))
    assert r['status'] == 'MATCH'
    assert not r['exact_same_id_geometry']


def test_same_id_alone_does_not_prove_correspondence():
    assert rules.classify(evidence(previous_status='same_id_geometry_changed'))['status'] == 'CONFLICT'


def test_split_correspondence_can_be_match_without_exact_highlight():
    r = rules.classify(evidence(coverage_ratio=.9627326578, supporting_count=2))
    assert r['status'] == 'MATCH'
    assert not r['exact_same_id_geometry']


def test_partial_sustained_path_remains_partial():
    assert rules.classify(evidence(coverage_ratio=.4, supporting_count=1))['status'] == 'PARTIAL_MATCH'


def test_crossing_micro_bend_is_not_partial():
    assert rules.classify(evidence(coverage_ratio=0, previous_status='nearby_no_sustained_overlap_at_1m'))['status'] == 'CONFLICT'


def test_regression_review_and_competing_paths_are_not_promoted():
    assert rules.classify(evidence(coverage_ratio=.7, supporting_count=1, regression_review_required=True))['status'] == 'CONFLICT'
    assert rules.classify(evidence(coverage_ratio=1, supporting_count=2, competing_overlap_m=3))['status'] == 'CONFLICT'


def test_clipped_evidence_cannot_claim_full_original_match():
    assert rules.classify(evidence(coverage_ratio=1, supporting_count=1, fully_inside=False))['status'] == 'CONFLICT'


def test_verified_full_exact_match_overrides_clipped_baseline_uncertainty():
    r = rules.classify(evidence(whole_geometry_equal=True, same_id_geometry_equal=True, fully_inside=False))
    assert r['status'] == 'MATCH'
    assert r['exact_same_id_geometry']


def test_unverified_source_join_fails_closed_even_if_id_matches():
    assert rules.classify(evidence(source_verified=False, whole_geometry_equal=False))['status'] == 'CONFLICT'
