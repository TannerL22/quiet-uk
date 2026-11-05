/* Saved places and side-by-side comparison for the England map.
   A saved place is only a name and coordinates. Its levels are always re-read
   from the verified release on the server, never stored in the browser. */
'use strict';
window.createEnglandPlaces = function ({manifest, describe, onShow, onChange, initialHash}) {
  const $ = id => document.getElementById(id);
  const KEY = 'quietuk-england-places-v1';
  const SOURCES = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const TIME = {Lden: '24-hour', Lday: 'Daytime', Lnight: 'Night'};
  const letter = i => String.fromCharCode(65 + i);
  let places = [], current = null, result = null, revision = 0, renameTimer, source = 'road', metric = 'Lden';

  function clean(input) {
    if (!Array.isArray(input) || input.length > 3) throw new Error('bad list');
    const seen = new Set();
    return input.map(p => {
      const label = typeof p?.label === 'string' ? p.label.trim() : '';
      if (!label || label.length > 60 || /[\x00-\x1f\x7f]/.test(label)) throw new Error('bad label');
      if (![p.longitude, p.latitude].every(v => typeof v === 'number' && Number.isFinite(v))
          || Math.abs(p.longitude) > 180 || Math.abs(p.latitude) > 90) throw new Error('bad coordinates');
      const key = `${p.longitude},${p.latitude}`;
      if (seen.has(key)) throw new Error('duplicate'); seen.add(key);
      return {longitude: p.longitude, latitude: p.latitude, label};
    });
  }
  const status = text => { $('places-status').textContent = text; };
  const query = () => new URLSearchParams({release: manifest.release_id, places: JSON.stringify(places)});
  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify({places})); }
    catch { status('This browser is not keeping saved places. Use “Copy comparison link” to keep them.'); }
    onChange(places);
  }
  function button(text, label, action, className = 'icon-button') {
    const b = document.createElement('button'); b.type = 'button'; b.className = className; b.textContent = text;
    b.setAttribute('aria-label', label); b.addEventListener('click', action); return b;
  }
  // View links keep 6 decimals (about 0.1 m), so match within that rather than exactly.
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  const isSaved = spot => spot && places.some(p => near(p.longitude, spot.longitude) && near(p.latitude, spot.latitude));
  function updateSave() {
    const saved = isSaved(current), full = places.length >= 3;
    for (const b of document.querySelectorAll('[data-save-place]')) {
      b.disabled = !current || saved || full;
      b.textContent = saved ? 'Saved ✓' : full ? 'Places full (3)' : 'Save to compare';
    }
  }
  function render() {
    const list = $('saved-places'); list.replaceChildren();
    places.forEach((p, i) => {
      const li = document.createElement('li'), badge = document.createElement('span'), input = document.createElement('input');
      badge.className = 'place-badge'; badge.textContent = letter(i); badge.setAttribute('aria-hidden', 'true');
      input.value = p.label; input.maxLength = 60; input.setAttribute('aria-label', `Name of place ${letter(i)}`);
      input.addEventListener('input', () => {
        try { places = clean(places.map((v, j) => j === i ? {...v, label: input.value} : v)); }
        catch { status('Names need 1 to 60 characters. The last good name is still saved.'); return; }
        ++revision; result = null; draw(); $('comparison-exports').hidden = true;
        status('Saved on this computer.'); persist();
        clearTimeout(renameTimer); renameTimer = setTimeout(refresh, 400);
      });
      const actions = document.createElement('div'); actions.className = 'saved-actions';
      actions.append(
        button('↗', `Show ${p.label} on the map`, () => onShow(p)),
        button('×', `Remove ${p.label}`, () => { places.splice(i, 1); changed(); }));
      li.append(badge, input, actions); list.append(li);
    });
    $('places-count').textContent = `${places.length} of 3`;
    $('open-comparison').disabled = places.length < 2;
    $('clear-places').hidden = !places.length;
    updateSave();
  }
  function changed() {
    clearTimeout(renameTimer); render();
    status(places.length >= 2 ? 'Saved on this computer. Rename a place by typing over its name.'
      : places.length ? 'Save one more spot to compare.' : 'Save up to three spots to compare them side by side.');
    persist(); if ($('comparison-dialog').open) refresh();
  }

  // Plain wording for "place minus A", including bounded differences.
  function differenceText(d, name) {
    if (d.difference_bounds_db) {
      // Round towards zero so the shortened bound never claims more than the evidence.
      const b = d.difference_bounds_db, weak = v => (Math.floor(Math.abs(v) * 10 + 1e-9) / 10).toFixed(1);
      if (b.lower_db !== null && b.lower_db >= 0) return `${name} is ${b.lower_inclusive ? 'at least' : 'more than'} ${weak(b.lower_db)} dB louder than A.`;
      if (b.upper_db !== null && b.upper_db <= 0) return `${name} is ${b.upper_inclusive ? 'at least' : 'more than'} ${weak(b.upper_db)} dB quieter than A.`;
      return `${name}: can't say which is louder, because one level is only known to be under the cutoff.`;
    }
    if (d.difference_from_first_db === null) {
      return d.status === 'bounds_do_not_rank' ? `${name}: both are under the cutoff, so they can't be ranked.`
        : d.status === 'reference_period_unspecified' ? `${name}: no comparison, because the data year isn't stated for this source.`
        : d.status === 'incompatible_evidence' ? `${name}: no comparison, because the evidence doesn't match.`
        : `${name}: no comparison, because one of the two has no published value.`;
    }
    const delta = d.difference_from_first_db;
    if (Math.abs(delta) < 0.05) return `${name} is the same as A${d.same_native_cell ? ' (the same 10 m square)' : ''}.`;
    return `${name} is ${Math.abs(delta).toFixed(1)} dB ${delta > 0 ? 'louder' : 'quieter'} than A.`;
  }
  function draw() {
    const table = $('compare-table'); table.replaceChildren(); $('comparison-differences').replaceChildren();
    if (!result) return;
    const caption = document.createElement('caption'); caption.className = 'visually-hidden';
    caption.textContent = 'Modelled yearly noise at each saved place, by source and time of day, in decibels'; table.append(caption);
    const head = document.createElement('thead'), hr = document.createElement('tr'), corner = document.createElement('th');
    corner.scope = 'col'; corner.textContent = ''; hr.append(corner);
    result.places.forEach((p, i) => {
      const th = document.createElement('th'), small = document.createElement('small');
      th.scope = 'col'; th.textContent = `${letter(i)} · ${p.label}`;
      small.textContent = `${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}`; th.append(small); hr.append(th);
    });
    head.append(hr); table.append(head);
    const body = document.createElement('tbody');
    for (const row of result.rows) {
      const tr = document.createElement('tr'), th = document.createElement('th');
      th.scope = 'row'; th.textContent = `${SOURCES[row.source]} · ${TIME[row.metric]}`; tr.append(th);
      if (row.source === source && row.metric === metric) tr.className = 'current';
      for (const o of row.observations) {
        const td = document.createElement('td'), d = describe(o);
        td.textContent = d.short; td.title = d.explain;
        if (o.value_db === null) td.className = 'missing';
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);
    const active = result.rows.find(r => r.source === source && r.metric === metric);
    $('comparison-focus').textContent = `${SOURCES[source]} noise · ${TIME[metric]}: compared with A`;
    active.differences_from_first.forEach((d, i) => {
      const li = document.createElement('li'); li.textContent = differenceText(d, `${letter(i + 1)} (${result.places[i + 1].label})`);
      $('comparison-differences').append(li);
    });
  }
  async function refresh() {
    const ticket = ++revision; result = null; draw(); $('comparison-exports').hidden = true;
    $('comparison-status').textContent = places.length < 2 ? 'Save at least two places to compare.' : 'Reading each place from the original data…';
    if (places.length < 2) return;
    const q = query().toString();
    try {
      const response = await fetch('/api/england/comparison?' + q, {signal: AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (ticket !== revision) return;
      if (data.release_id !== manifest.release_id) throw new Error();
      result = data; draw();
      $('comparison-status').textContent = 'Defra’s modelled yearly averages for 2021, rounded to 0.1 dB. The downloads keep full precision and evidence.';
      $('comparison-csv').href = '/downloads/england-comparison.csv?' + q;
      $('comparison-json').href = '/downloads/england-comparison.json?' + q;
      $('comparison-exports').hidden = false;
    } catch {
      if (ticket === revision) $('comparison-status').textContent = 'The comparison couldn’t be loaded or checked. Close this and try again.';
    }
  }

  for (const b of document.querySelectorAll('[data-save-place]')) b.addEventListener('click', () => save());
  function save() {
    if (!current || isSaved(current) || places.length >= 3) return;
    places.push({longitude: +current.longitude.toFixed(6), latitude: +current.latitude.toFixed(6), label: current.label.slice(0, 60)});
    changed();
  }
  $('clear-places').addEventListener('click', () => { places = []; changed(); });
  $('open-comparison').addEventListener('click', () => { $('comparison-dialog').showModal(); refresh(); });
  $('copy-comparison').addEventListener('click', async () => {
    const url = new URL(window.location.href), hash = new URLSearchParams(url.hash.slice(1));
    hash.set('compare', JSON.stringify(places)); url.hash = hash.toString();
    try { await navigator.clipboard.writeText(url.href); $('comparison-share-status').textContent = 'Link copied. It works on this computer while Quiet UK is running.'; }
    catch { history.replaceState(null, '', url); $('comparison-share-status').textContent = 'Copy the address from your browser to share this comparison.'; }
  });

  try {
    const fromLink = initialHash.get('compare');
    const saved = fromLink !== null ? {places: JSON.parse(fromLink)} : JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved) places = clean(saved.places);
  } catch { status('Saved places couldn’t be read, so the list starts empty.'); }
  render(); onChange(places);

  return {
    places: () => places.map(p => ({...p})),
    save,
    update(spot, selectedSource, selectedMetric) {
      current = spot; source = selectedSource; metric = selectedMetric; updateSave(); draw();
    },
  };
};
