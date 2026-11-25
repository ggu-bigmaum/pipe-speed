import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import comparison_keys as keys


def test_actual_tile_fields_are_not_original_pipe_ids():
    assert keys.tile_key('kict', {'FTR_IDN': 5602301010, 'STD_DIP': 20., 'MOP_CDE':'MOP006'}) == '["5602301010","20","MOP006"]'


def test_fractional_diameter_and_text_identifier_preserved():
    assert keys.tile_key('seoul', {'PLINE_ID':'00123','PLINE_CALB':.25,'PLINE_MTRQ':'0'}) == '["00123","0.25","0"]'


def test_null_is_different_from_empty_and_zero():
    assert keys.tile_key('seoul', {}) == '[null,null,null]'
    assert keys.tile_key('seoul', {'PLINE_ID':'','PLINE_CALB':0,'PLINE_MTRQ':''}) == '["","0",""]'


def test_source_key_cannot_use_unverified_layers_or_unsafe_numbers():
    import pytest
    with pytest.raises(ValueError): keys.tile_key('sewer', {})
    with pytest.raises(ValueError): keys.tile_key('kict', {'FTR_IDN':2**54})


def test_unobserved_numbers_cannot_round_into_an_existing_key():
    import pytest
    with pytest.raises(ValueError): keys.tile_key('kict', {'STD_DIP':100.00000000000001})
    with pytest.raises(ValueError): keys.tile_key('kict', {'STD_DIP':.43000000000000005})
