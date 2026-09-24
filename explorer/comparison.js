/* Saved coordinates are local recipes. Values are re-read from verified rasters. */
'use strict';
window.createPlaceComparison = function ({manifest, onShow, onChange, initialHash}) {
  const $ = id => document.getElementById(id);
  const storageKey = 'quietuk-place-comparison-v1';
  const names = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const metricNames = {Lden: 'Lden', Lday: 'Day', Lnight: 'Night'};
  let places = [], current, result, revision = 0, renameTimer, source = 'aircraft', metric = 'Lden';
  function clean(input) {
    if (!Array.isArray(input) || input.length > 3) throw new Error();
    const seen = new Set();
    return input.map(p => {
      if (!p || !Object.hasOwn(manifest.sites, p.site) || typeof p.label !== 'string' || !p.label.trim() || p.label.trim().length > 60 || /[\x00-\x1f\x7f]/.test(p.label) ||
          ![p.longitude, p.latitude].every(x => typeof x === 'number' && Number.isFinite(x)) || Math.abs(p.longitude) > 180 || Math.abs(p.latitude) > 90) throw new Error();
      const key = JSON.stringify([p.site, p.longitude, p.latitude]);
      if (seen.has(key)) throw new Error(); seen.add(key);
      return {site: p.site, longitude: p.longitude, latitude: p.latitude, label: p.label.trim()};
    });
  }
  const query = () => new URLSearchParams({release: manifest.release_id, places: JSON.stringify(places)});
  function status(text) { $('saved-status').textContent = text; }
  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify({release_id: manifest.release_id, places})); }
    catch { status('Browser storage is unavailable. Copy the comparison link to keep these places.'); }
    onChange();
  }
  function button(text, label, action) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.setAttribute('aria-label', label);
    b.addEventListener('click', action); return b;
  }
  function updateAdd() {
    const duplicate = current && places.some(p => p.site === current.site && p.longitude === current.longitude && p.latitude === current.latitude);
    $('save-place').disabled = !current || places.length === 3 || duplicate;
    $('save-place').textContent = duplicate ? 'Added to comparison' : places.length === 3 ? 'Comparison full · 3 places' : '＋ Add to comparison';
  }
  function renderPlaces() {
    $('saved-places').replaceChildren();
    places.forEach((p, i) => {
      const li = document.createElement('li'), badge = document.createElement('span'), input = document.createElement('input');
      badge.className = 'place-badge'; badge.textContent = String.fromCharCode(65+i);
      input.value = p.label; input.maxLength = 60; input.setAttribute('aria-label', `Name for place ${badge.textContent}`);
      input.addEventListener('input', () => {
        try { places = clean(places.map((v, j) => j === i ? {...v, label: input.value} : v)); }
        catch { status('Use a name with 1–60 characters. The last valid name is still saved.'); return; }
        // Keep focus while typing and invalidate in-flight exports immediately.
        ++revision; result = null; renderResult(); $('comparison-exports').hidden = true;
        status('Saved in this browser. Rename a place above.'); persist();
        actions.children[0].setAttribute('aria-label', `Show ${places[i].label} on map`);
        actions.children[1].setAttribute('aria-label', `Remove ${places[i].label}`);
        clearTimeout(renameTimer); renameTimer = setTimeout(refresh, 400);
      });
      const actions = document.createElement('div'); actions.className = 'saved-actions';
      actions.append(button('Show', `Show ${p.label} on map`, () => onShow(p)), button('×', `Remove ${p.label}`, () => {places.splice(i,1); changed();}));
      li.append(badge, input, actions); $('saved-places').append(li);
    });
    $('open-comparison').disabled = places.length < 2;
    $('clear-comparison').hidden = !places.length;
    $('saved-count').textContent = `${places.length} / 3`;
    updateAdd();
  }
  function changed() {
    clearTimeout(renameTimer);
    renderPlaces(); status(places.length ? 'Saved in this browser. Rename a place above.' : 'Add two or three places to compare.');
    persist(); refresh();
  }
  function observationText(o) {
    return o.value_db !== null ? `${o.value_db.toFixed(1)} dB(A)` : o.status === 'outside_pilot' ? 'Outside coverage' : 'Unreported';
  }
  function renderResult() {
    const table = $('places-table'); table.replaceChildren(); $('comparison-differences').replaceChildren();
    if (!result) return;
    const caption = document.createElement('caption'); caption.textContent = 'Annual modelled noise by source and time indicator'; table.append(caption);
    const head = document.createElement('thead'), header = document.createElement('tr'), corner = document.createElement('th');
    corner.scope = 'col'; corner.textContent = 'Source / time'; header.append(corner);
    result.places.forEach((p,i) => {
      const th = document.createElement('th'), label = document.createElement('span'), coords = document.createElement('small');
      th.scope = 'col'; label.textContent = `${String.fromCharCode(65+i)} · ${p.label}`;
      coords.textContent = `${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}`;
      th.append(label,coords); header.append(th);
    });
    head.append(header); table.append(head);
    const body = document.createElement('tbody');
    for (const row of result.rows) {
      const tr = document.createElement('tr'), th = document.createElement('th'), period = document.createElement('small');
      th.scope = 'row'; th.textContent = `${names[row.source]} · ${metricNames[row.metric]}`;
      period.textContent = row.reference_period ? `${row.reference_period.start.slice(0,4)} reference` : 'Period unspecified';
      th.append(period); tr.append(th);
      if (row.source === source && row.metric === metric) tr.className = 'selected-indicator';
      for (const o of row.observations) {
        const td = document.createElement('td'); td.textContent = observationText(o);
        if (o.value_db === null) td.className = 'missing-observation';
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);
    const active = result.rows.find(r => r.source === source && r.metric === metric);
    $('comparison-focus').textContent = `${names[source]} · ${metricNames[metric]} · difference from A`;
    active.differences_from_first.forEach((d,i) => {
      const p = document.createElement('p'), letter = String.fromCharCode(66+i);
      if (d.difference_from_first_db === null) p.textContent = `${letter}: ${d.status === 'reference_period_unspecified' ? 'Reference period unspecified; no numerical comparison.' : d.status === 'incompatible_evidence' ? 'Incompatible evidence; no numerical comparison.' : 'Unreported or outside coverage; difference unavailable.'}`;
      else {
        const delta = d.difference_from_first_db;
        p.textContent = `${letter}: ${delta > 0 ? '+' : ''}${delta.toFixed(1)} dB${d.same_native_cell ? ' · same model cell as A' : ' · modelled difference'}.`;
      }
      $('comparison-differences').append(p);
    });
  }
  async function refresh() {
    const ticket = ++revision; result = null; renderResult();
    $('comparison-exports').hidden = true;
    $('comparison-status').textContent = places.length < 2 ? 'Add at least two places to compare.' : 'Reading original cells and checking their evidence…';
    if (places.length < 2) return;
    const requestQuery = query().toString();
    try {
      const response = await fetch('/api/pilot/comparison?'+requestQuery, {signal: AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (ticket !== revision) return;
      if (data.release_id !== manifest.release_id) throw new Error();
      result = data; renderResult();
      $('comparison-status').textContent = 'Original 10 m cells · rounded here to 0.1 dB; exports retain full precision.';
      $('comparison-json').href = '/downloads/pilot-comparison.json?'+requestQuery;
      $('comparison-csv').href = '/downloads/pilot-comparison.csv?'+requestQuery;
      $('comparison-exports').hidden = false;
    } catch {
      if (ticket === revision) $('comparison-status').textContent = 'Comparison unavailable: the data could not be verified or loaded. Close and reopen to retry.';
    }
  }
  $('save-place').addEventListener('click', () => {
    if ($('save-place').disabled) return;
    places.push({site: current.site, longitude: current.longitude, latitude: current.latitude,
      label: `${manifest.sites[current.site].name.split(' · ')[0]} · ${current.latitude.toFixed(4)}, ${current.longitude.toFixed(4)}`});
    changed();
  });
  $('clear-comparison').addEventListener('click', () => {places = []; changed();});
  $('open-comparison').addEventListener('click', () => {$('comparison-dialog').showModal(); refresh();});
  $('close-comparison').addEventListener('click', () => $('comparison-dialog').close());
  $('copy-comparison').addEventListener('click', async () => {
    const url = new URL(window.location.href), hash = new URLSearchParams(url.hash.slice(1));
    hash.set('v', manifest.release_id); hash.set('compare', JSON.stringify(places)); url.hash = hash.toString();
    try { await navigator.clipboard.writeText(url.href); $('comparison-share-status').textContent = 'Link copied. It works on a computer serving this same release.'; }
    catch { $('comparison-share-status').textContent = 'Clipboard unavailable. The comparison is saved in this page address; copy it from your browser.'; history.replaceState(null,'',url); }
  });
  try {
    const fromLink = initialHash.get('compare');
    const saved = fromLink !== null ? {release_id: initialHash.get('v'), places: JSON.parse(fromLink)} : JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved && saved.release_id !== manifest.release_id) status('Saved places belong to a different data release and were not loaded.');
    else if (saved) {places = clean(saved.places); status(places.length ? 'Saved places restored; values are checked again.' : 'Add two or three places to compare.');}
  } catch {status('Saved comparison could not be read. Add places to start a new one.');}
  renderPlaces(); refresh();
  return {
    getPlaces: () => places.map(p => ({...p})),
    update: (location, selectedSource, selectedMetric) => {
      current = location; source = selectedSource; metric = selectedMetric; updateAdd(); renderResult();
    }
  };
};
