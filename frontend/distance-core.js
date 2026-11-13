(function (root) {
  'use strict';
  function cellAt(xy, meta) {
    if (!xy.every(Number.isFinite)) return null;
    const col = Math.floor((xy[0] - meta.origin[0]) / meta.pixel_size[0]);
    const row = Math.floor((meta.origin[1] - xy[1]) / meta.pixel_size[1]);
    return col >= 0 && col < meta.width && row >= 0 && row < meta.height ? {col, row} : null;
  }
  function validStyle(breaks, colors) {
    return breaks.length === 4 && colors.length === 5 && breaks.every((v, i) => Number.isFinite(v) && v >= 0 && (!i || v > breaks[i - 1])) && colors.every(c => /^#[0-9a-f]{6}$/i.test(c));
  }
  const api = {cellAt, validStyle};
  if (typeof module !== 'undefined') module.exports = api;
  else root.PipeDistanceCore = api;
})(typeof window !== 'undefined' ? window : globalThis);
