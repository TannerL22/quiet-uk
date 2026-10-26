(() => {
  'use strict';
  const intersects = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
  function plan(records, view, budget, overviewPixels) {
    const bounds = view.bounds;
    const visible = records.filter(r => intersects(r.bounds, bounds));
    if (!visible.length) return {mode:'outside', records:[], pixels:0};
    const allowance = budget.max_decoded_pixels - overviewPixels;
    const pixels = r => r.width * r.height;
    const visiblePixels = visible.reduce((sum, r) => sum + pixels(r), 0);
    const scale = Math.min(...visible.map(r => Math.max(
      view.width / (bounds[2]-bounds[0]) * (r.bounds[2]-r.bounds[0]) / r.width,
      view.height / (bounds[3]-bounds[1]) * (r.bounds[3]-r.bounds[1]) / r.height)));
    if (visible.length > budget.max_images || visiblePixels > allowance || scale < .35) {
      return {mode:'overview', records:[], pixels:overviewPixels};
    }
    // Visible cores always have priority. Add nearby cores only within the same
    // budget, so a margin cannot force a partially detailed viewport.
    const dx = (bounds[2]-bounds[0])*.1, dy = (bounds[3]-bounds[1])*.1;
    const margin = [bounds[0]-dx, bounds[1]-dy, bounds[2]+dx, bounds[3]+dy];
    const selected = [...visible];
    let total = visiblePixels + overviewPixels;
    for (const r of records) {
      if (!visible.includes(r) && intersects(r.bounds, margin)
          && selected.length < budget.max_images && total + pixels(r) <= budget.max_decoded_pixels) {
        selected.push(r); total += pixels(r);
      }
    }
    return {mode:'detail', records:selected, pixels:total};
  }
  const pointRecipe = coords => coords.map(String).join(',');
  function requireRelease(data, release) {
    if (data?.release_id !== release) throw new Error('release_mismatch');
    return data;
  }
  function legendPattern(item) {
    const colour = /^#[a-f0-9]{6}$/i.test(item.colour) ? item.colour : '#c0b8a5';
    const angle = item.encoding === 'tiff_nodata' ? '45deg' : '135deg';
    return `repeating-linear-gradient(${angle}, ${colour} 0 2px, ${colour}88 2px 8px)`;
  }
  const api = {plan, pointRecipe, legendPattern, requireRelease};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else window.QuietPilotDisplay = api;
})();
