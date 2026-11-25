"""Exact canonical keys shared by the web API and offline validation.

These are groups, not unique original IDs. Never select the first member of a
duplicate group or assign an all-group flag from just one member.
"""
import json
import math

FIELDS = {
    'kict': ('FTR_IDN', 'STD_DIP', 'MOP_CDE'),
    'seoul': ('PLINE_ID', 'PLINE_CALB', 'PLINE_MTRQ'),
}


def scalar(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('Boolean is not a water tile key value')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) > 2**53 - 1:
            raise ValueError('Unsafe tile key number')
        # Source/MVT audit: IDs are safe integers, diameters use at most two
        # decimals in 0..9999. Do not round unknown numbers into known keys.
        text = str(int(value)) if value == int(value) else format(value, '.15g')
        if float(text) != value:
            raise ValueError('Tile key number requires unverified precision')
        return text
    if not isinstance(value, str):
        raise ValueError('Tile key values must be scalar')
    return value


def tile_key(dataset, properties):
    if dataset not in FIELDS:
        raise ValueError('Unsupported comparison dataset')
    return json.dumps([scalar(properties.get(k)) for k in FIELDS[dataset]], ensure_ascii=False, separators=(',', ':'))
