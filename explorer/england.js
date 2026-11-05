(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const SOURCES = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const METRICS = ['Lden', 'Lday', 'Lnight'];
  const TIME = {Lden: '24-hour average', Lday: 'Daytime average', Lnight: 'Night-time average'};
  const TIME_SHORT = {Lden: '24-hour', Lday: 'Daytime', Lnight: 'Night'};
  const NOTES = {
    Lden: 'Yearly average over the whole day, with evening and night noise counted as louder because it bothers people more.',
    Lday: 'Yearly average from 7am to 7pm.',
    Lnight: 'Yearly average from 11pm to 7am.',
  };
  const BASEMAP = {light: 'https://tiles.openfreemap.org/styles/positron', dark: 'https://tiles.openfreemap.org/styles/dark'};
  const ENGLAND = [[-6.0, 49.9], [1.8, 55.82]];
  // Close up, exact native-cell images replace the zoom-14 tiles, which can
  // shift a cell edge by up to one tile pixel (about 6 m).
  const CELL_ZOOM = 15.5;
  const empty = {type: 'FeatureCollection', features: []};
  const theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  const phone = window.matchMedia('(max-width: 760px)');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const storage = {
    get: key => { try { return localStorage.getItem(key); } catch { return null; } },
    set: (key, value) => { try { localStorage.setItem(key, value); } catch { /* Private browsing: nothing to keep. */ } },
  };

  let manifest, look, app, map, places, popup, spotMarker;
  let spot = null, point = null, placeName = null;
  let source = 'road', metric = 'Lden', selection = 0, navigation = 0;
  let cellLayers = [], cellTicket = 0, ready = false, pendingFly = false, firstSymbol;
  let savedMarkers = [], recorded = null, recordedMarkers = [];
  const hash = new URLSearchParams(window.location.hash.slice(1));

  // ---------- small helpers ----------
  const message = text => { $('message').textContent = text || ''; $('message').hidden = !text; };
  function loading(text) {
    $('loading-text').textContent = text || 'Loading the noise map…';
    $('loading').classList.toggle('is-done', !text);
  }
  async function getJSON(url, ms = 15000) {
    const response = await fetch(url, {signal: AbortSignal.timeout(ms)});
    if (response.status === 409) throw new Error('release_mismatch');
    if (!response.ok) throw new Error('http_' + response.status);
    return response.json();
  }
  const tileUrl = () => `/england-tiles/${manifest.release_id}/${source}-${metric}/{z}/{x}/{y}.png?theme=${theme}`;
  const cutoff = (s, m) => manifest.evidence_contract.zero_rule_sources.includes(s) ? (m === 'Lnight' ? 35 : 40) : null;
  const palette = () => look.themes[theme];
  const bandIndex = value => look.breaks_db.filter(b => value >= b).length;
  function wordFor(value) {
    const found = look.words.find(([low, high]) => (low === null || value >= low) && (high === null || value < high));
    return found ? found[2] : '';
  }
  function shortName(name) {
    const parts = name.split(',').map(s => s.trim()).filter(s => s && !['England', 'United Kingdom'].includes(s));
    if (parts.length > 1 && /^\d+[a-z]?$/i.test(parts[0])) parts.splice(0, 2, `${parts[0]} ${parts[1]}`);
    return parts.slice(0, 2).join(', ').slice(0, 60);
  }
  // A clicked spot has no name; its position tells saved spots apart.
  const spotLabel = () => placeName || `Spot at ${spot.latitude.toFixed(4)}, ${spot.longitude.toFixed(4)}`;
  const plainSource = s => s === 'road' ? 'road traffic' : s === 'rail' ? 'trains' : 'aircraft';

  // Everything the page says about one observation, from its evidence status.
  function describe(o) {
    if (o.value_db !== null) {
      // Night words sit 10 dB lower: the same weight the 24-hour measure gives night noise.
      const word = wordFor(o.metric === 'Lnight' ? o.value_db + 10 : o.value_db);
      return {kind: 'value', value: o.value_db.toFixed(1), unit: 'dB', short: o.value_db.toFixed(1), word,
              colour: palette().bands[bandIndex(o.value_db)],
              explain: `${word}: a yearly average of ${o.value_db.toFixed(1)} dB from ${plainSource(o.source)}, as modelled by Defra for this 10 m square.`};
    }
    if (o.status === 'verified_below_cutoff') {
      const limit = o.bounds.upper_db;
      return {kind: 'below', value: `Under ${limit}`, unit: 'dB', short: `<${limit}`, word: 'Very quiet', colour: palette().below,
              explain: `Very quiet for ${plainSource(o.source)}: Defra's model worked out a level under ${limit} dB here, but doesn't publish the exact number.`};
    }
    if (o.status === 'outside_extract') {
      return {kind: 'none', value: 'Not mapped', unit: '', short: '—', word: '',
              explain: 'This spot is outside the mapped area. The map covers England only.'};
    }
    if (o.source === 'aircraft') {
      return {kind: 'none', value: 'Not mapped', unit: '', short: '—', word: '',
              explain: 'Aircraft noise is only mapped around major airports, and Defra publishes no value here. That doesn’t mean it’s quiet.'};
    }
    return {kind: 'none', value: 'No data', unit: '', short: '—', word: '',
            explain: 'Defra publishes no value for this square. That doesn’t mean it’s quiet.'};
  }
  function guideline(o) {
    const limit = look.who_2018[o.source]?.[o.metric];
    if (!limit) return '';
    const name = `the WHO's recommended limit for ${o.source === 'road' ? 'road' : o.source === 'rail' ? 'rail' : 'aircraft'} noise (${limit} dB)`;
    if (o.value_db !== null) return o.value_db > limit ? `Above ${name}.` : `Below ${name}.`;
    if (o.status === 'verified_below_cutoff' && o.bounds.upper_db <= limit) return `Below ${name}.`;
    return '';
  }
  const active = () => spot?.observations.find(o => o.source === source && o.metric === metric);

  // ---------- view state in the address ----------
  function saveView() {
    if (!manifest) return;
    const params = new URLSearchParams({source, metric, v: manifest.release_id, noise: $('overlay').checked ? '1' : '0'});
    if (map) { const c = map.getCenter(); params.set('camera', [c.lng.toFixed(6), c.lat.toFixed(6), map.getZoom().toFixed(2)].join(',')); }
    if (point) params.set('point', point.map(v => v.toFixed(6)).join(','));
    if (point && placeName) params.set('label', placeName);
    if (recorded && $('recorded-toggle').checked) params.set('samples', '1');
    history.replaceState(null, '', '#' + params.toString());
  }

  // ---------- legend, notes and the help guide ----------
  function legendSteps() {
    const p = palette(), limit = cutoff(source, metric), labels = ['<40', '40', '45', '50', '55', '60', '65', '70+'];
    const steps = [];
    if (limit !== null) steps.push({colour: p.below, label: `<${limit}`, word: `Under ${limit} dB`});
    p.bands.forEach((colour, i) => {
      if (i === 0 && limit === 40) return; // Road and rail daytime levels start at 40.
      steps.push({colour, label: i === 0 && limit === 35 ? '35' : labels[i],
                  word: i === 0 ? (limit === 35 ? '35–40 dB' : 'Under 40 dB') : i === 7 ? '70 dB and over' : `${look.breaks_db[i - 1]}–${look.breaks_db[i]} dB`});
    });
    return steps;
  }
  function hatch(colour) { return `repeating-linear-gradient(45deg, ${colour} 0 2px, transparent 2px 5px)`; }
  function drawLegend() {
    $('legend-title').textContent = `${SOURCES[source]} noise · ${TIME[metric]}`;
    const ramp = $('legend-ramp'); ramp.replaceChildren();
    for (const step of legendSteps()) {
      const div = document.createElement('div'), swatch = document.createElement('i'), label = document.createElement('span');
      div.className = 'legend-step'; div.title = step.word; swatch.style.background = step.colour; label.textContent = step.label;
      div.append(swatch, label); ramp.append(div);
    }
    const extra = $('legend-extra'); extra.replaceChildren();
    const item = (background, text) => {
      const span = document.createElement('span'), i = document.createElement('i');
      i.style.background = background; i.style.border = '1px solid var(--line-strong)'; span.append(i, text); extra.append(span);
    };
    item(hatch(palette().no_data), source === 'aircraft' ? 'Not mapped (aircraft: airports only)' : 'No published value');
    $('layer-note').textContent = NOTES[metric] + (source === 'aircraft' ? ' Aircraft noise is only mapped around major airports.' : '');
  }
  function drawHelpScale() {
    const body = $('help-scale').tBodies[0]; body.replaceChildren();
    const p = palette();
    const rows = [[p.below, 'Under 40 dB (under 35 at night)', 'Very quiet for transport noise. Exact level not published.']];
    p.bands.slice(1).forEach((colour, i) => {
      const low = look.breaks_db[i], high = look.breaks_db[i + 1];
      rows.push([colour, high ? `${low}–${high} dB` : `${low} dB and over`, wordFor(low)]);
    });
    rows.push([hatch(p.no_data), 'Grey stripes', 'No published value. Not the same as quiet.']);
    for (const [colour, range, word] of rows) {
      const tr = document.createElement('tr'), a = document.createElement('td'), b = document.createElement('td'), c = document.createElement('td');
      const i = document.createElement('i'); i.style.background = colour; i.style.border = '1px solid var(--line-strong)'; a.append(i);
      b.textContent = range; c.textContent = word; tr.append(a, b, c); body.append(tr);
    }
  }
  function overlayStatus(exact) {
    if (!$('overlay').checked) { $('overlay-status').textContent = 'Noise colours hidden.'; return; }
    const zoom = map ? map.getZoom() : 0;
    $('overlay-status').textContent = exact ? 'Showing Defra’s exact 10 m squares.'
      : zoom < 13.5 ? 'Zoomed out, the colours are a sample of the 10 m squares. Zoom in to see them all.'
      : cellMode() ? 'Loading the exact 10 m squares…' : 'Showing every 10 m square.';
  }

  // ---------- research section ----------
  function definition(list, term, ...values) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = term; dd.append(...values); list.append(dt, dd);
  }
  function link(href, text) { const a = document.createElement('a'); a.href = href; a.target = '_blank'; a.rel = 'noopener'; a.textContent = text; return a; }
  function mono(text) { const span = document.createElement('span'); span.className = 'mono'; span.textContent = text; return span; }
  function drawResearch() {
    const indicators = $('research-indicators'); indicators.replaceChildren();
    for (const m of METRICS) definition(indicators, m, manifest.metrics[m]);
    const data = $('research-data'); data.replaceChildren();
    const a = manifest.acquisition;
    definition(data, 'Coverage', a.status === 'sealed' ? `All of England: ${a.planned_tiles.toLocaleString()} squares of 10 km, checked against the original downloads.`
      : `Preview: ${a.complete_tiles.toLocaleString()} of ${a.planned_tiles.toLocaleString()} squares downloaded so far.`);
    for (const [key, name] of Object.entries(SOURCES)) {
      const product = manifest.products[key], period = product.reference_period;
      definition(data, name, period ? `${period.start}–${period.end} (provider declared) · ` : 'Reference period not stated by the provider · ',
        link('https://environment.data.gov.uk/dataset/' + product.metadata_id, 'Provider record ↗'));
    }
    definition(data, 'Grid', `${manifest.spatial_resolution_m} m cells, ${manifest.receiver_height_m} m above ground, ${manifest.units}`);
    definition(data, 'Release', mono(manifest.release_id));
    definition(data, 'Evidence contract', `Version ${manifest.evidence_contract.version}`);
    definition(data, 'Licence', link(manifest.licence_url, manifest.licence), document.createTextNode('. ' + manifest.attribution));
    $('research-policy').textContent = manifest.evidence_contract.missing_value_policy + ' ' + manifest.uncertainty;
    const drawing = $('research-drawing'); drawing.replaceChildren();
    const t = manifest.transformation, ostn15 = (t.grids || []).some(g => g.name.includes('OSTN15'));
    for (const text of [
      `Zoomed out, ${manifest.display.method}, so thin roads can be skipped. From zoom ${CELL_ZOOM} the original cells are drawn exactly, and clicking always reads the original cell.`,
      `Map positions use ${ostn15 ? "Ordnance Survey's OSTN15 grid" : t.description} (stated accuracy ${t.stated_accuracy_m} m).` + (t.better_operations_not_installed.length ? ' A more accurate grid is available but not installed.' : ''),
      `Colours (${look.version}): one violet hue, lighter for quieter and darker for louder, chosen to stay readable with colour-blindness. ${look.note}`,
    ]) { const li = document.createElement('li'); li.textContent = text; drawing.append(li); }
  }
  function drawResearchSpot() {
    const list = $('research-spot'); list.replaceChildren();
    const o = active();
    $('research-spot-empty').hidden = !!spot; list.hidden = !spot; $('point-download').hidden = !spot;
    if (!spot) return;
    definition(list, 'Latitude, longitude', mono(`${spot.latitude.toFixed(6)}, ${spot.longitude.toFixed(6)}`));
    if (spot.easting !== null) definition(list, 'British National Grid', mono(`${spot.easting.toFixed(1)} E, ${spot.northing.toFixed(1)} N`));
    definition(list, 'Defra square', mono(spot.tile || 'none'));
    definition(list, 'Layer', `${o.source}-${o.metric}`);
    definition(list, 'Status', mono(o.status));
    definition(list, 'Stored value', mono(o.raw_value === null ? 'none' : String(o.raw_value)));
    if (o.row !== null) definition(list, 'Native cell', mono(`row ${o.row}, column ${o.column}`));
    if (o.record) {
      definition(list, 'Record SHA-256', mono(o.record.sha256));
      definition(list, 'Archive', mono(o.record.path));
    }
    definition(list, 'Transformation', spot.transformation);
    $('point-download').href = '/downloads/england-location.json?' + new URLSearchParams({lon: spot.longitude, lat: spot.latitude, release: manifest.release_id});
  }

  // ---------- the selected spot ----------
  function readingNodes(d) {
    const value = document.createElement('span');
    value.textContent = d.value;
    if (d.unit) { const small = document.createElement('small'); small.textContent = ' ' + d.unit; value.append(small); }
    const chip = document.createElement('span'); chip.className = 'level-chip';
    if (d.word) { const i = document.createElement('i'); i.style.background = d.colour; chip.append(i, d.word); }
    return [value, chip];
  }
  function drawSpot() {
    const section = $('spot');
    places?.update(spot ? {longitude: spot.longitude, latitude: spot.latitude, label: spotLabel()} : null, source, metric);
    if (!spot) { section.hidden = true; drawResearchSpot(); return; }
    section.hidden = false;
    const o = active(), d = describe(o);
    $('spot-title').textContent = spotLabel();
    const [value, chip] = readingNodes(d);
    $('spot-value').replaceChildren(...value.childNodes);
    $('spot-level').replaceChildren(...chip.childNodes);
    $('spot-explain').textContent = d.explain;
    $('spot-who').textContent = guideline(o);
    const body = $('readings'); body.replaceChildren();
    for (const key of Object.keys(SOURCES)) {
      const tr = document.createElement('tr'), th = document.createElement('th');
      th.scope = 'row'; th.textContent = SOURCES[key]; tr.append(th);
      for (const m of METRICS) {
        const ob = spot.observations.find(x => x.source === key && x.metric === m), od = describe(ob);
        const td = document.createElement('td'), button = document.createElement('button');
        button.type = 'button'; button.textContent = od.short; button.title = od.explain;
        button.setAttribute('aria-label', `${SOURCES[key]}, ${TIME_SHORT[m]}: ${od.value}${od.unit ? ' ' + od.unit : ''}. Show on map.`);
        button.setAttribute('aria-pressed', String(key === source && m === metric));
        if (ob.value_db === null) button.classList.add('missing');
        button.addEventListener('click', () => choose(key, m));
        td.append(button); tr.append(td);
      }
      body.append(tr);
    }
    if (ready) map.getSource('selected-cell').setData(o.cell ? {type: 'Feature', properties: {}, geometry: o.cell} : empty);
    drawResearchSpot();
    drawPopup();
  }
  function drawPopup() {
    if (!spot || !popup?.isOpen()) return;
    const o = active(), d = describe(o), root = document.createElement('div');
    const title = document.createElement('p'); title.className = 'popup-title'; title.textContent = spotLabel();
    const reading = document.createElement('div'); reading.className = 'popup-reading';
    const [value, chip] = readingNodes(d); value.className = 'popup-value'; reading.append(value, chip);
    const sub = document.createElement('p'); sub.className = 'popup-sub'; sub.textContent = `${SOURCES[source]} noise · ${TIME[metric]}`;
    root.append(title, reading, sub);
    const who = guideline(o) || (d.kind === 'none' ? d.explain : '');
    if (who) { const p = document.createElement('p'); p.className = 'popup-who'; p.textContent = who; root.append(p); }
    const actions = document.createElement('div'); actions.className = 'popup-actions';
    const details = document.createElement('button'); details.type = 'button'; details.className = 'button'; details.textContent = 'Details';
    details.addEventListener('click', showDetails);
    const save = document.createElement('button'); save.type = 'button'; save.className = 'button button-quiet'; save.dataset.savePlace = '';
    save.addEventListener('click', () => places.save());
    actions.append(details, save); root.append(actions);
    popup.setDOMContent(root);
    places.update({longitude: spot.longitude, latitude: spot.latitude, label: spotLabel()}, source, metric);
  }
  function openPopup(text) {
    if (!popup) {
      popup = new maplibregl.Popup({closeButton: true, closeOnClick: false, maxWidth: '300px', offset: 16, focusAfterOpen: false});
    }
    const p = document.createElement('p'); p.className = 'popup-sub'; p.textContent = text;
    popup.setLngLat(point).setDOMContent(p);
    if (!popup.isOpen()) popup.addTo(map);
  }
  function showDetails() {
    if (phone.matches) sheet('open');
    $('spot').scrollIntoView({block: 'start', behavior: reduceMotion.matches ? 'auto' : 'smooth'});
    $('spot-title').focus({preventScroll: true});
  }
  // The marker and popup need the map; the lookup itself does not, so it never waits for the basemap.
  function showOnMap() {
    if (!ready || !point) return;
    if (!spotMarker) {
      const el = document.createElement('div'); el.className = 'spot-marker';
      spotMarker = new maplibregl.Marker({element: el});
    }
    spotMarker.setLngLat(point).addTo(map);
    openPopup('Checking this spot…');
    if (spot) drawSpot(); else map.getSource('selected-cell').setData(empty);
  }
  async function inspect(coords, name = null) {
    const current = ++selection;
    point = coords; placeName = name; spot = null;
    hideTip(); showOnMap(); saveView();
    try {
      const data = await getJSON('/api/england/location?' + new URLSearchParams({lon: coords[0], lat: coords[1], release: manifest.release_id}));
      if (current !== selection) return;
      if (data.release_id !== manifest.release_id) throw new Error('release_mismatch');
      spot = data; drawSpot();
    } catch (error) {
      if (current !== selection) return;
      spot = null; drawSpot();
      const text = error.message === 'release_mismatch'
        ? 'The map data has been updated. Reload the page to check this spot.'
        : 'This spot couldn’t be loaded or checked. Try again.';
      if (ready) openPopup(text); else message(text);
    }
  }

  // ---------- map layers ----------
  const cellMode = () => $('overlay').checked && map.getZoom() >= CELL_ZOOM;
  function refreshCells() {
    if (!map.getLayer('noise')) return;
    const exact = cellMode() && cellLayers.length > 0 && cellLayers.every(id => map.isSourceLoaded(id));
    const tiles = $('overlay').checked && !exact ? 'visible' : 'none';
    if (map.getLayoutProperty('noise', 'visibility') !== tiles) map.setLayoutProperty('noise', 'visibility', tiles);
    for (const id of cellLayers) {
      const shown = cellMode() ? 'visible' : 'none';
      if (map.getLayoutProperty(id, 'visibility') !== shown) map.setLayoutProperty(id, 'visibility', shown);
    }
    overlayStatus(exact);
  }
  async function syncCells() {
    const ticket = ++cellTicket;
    let windows = [];
    if (cellMode()) {
      const b = map.getBounds(), dx = (b.getEast() - b.getWest()) * .1, dy = (b.getNorth() - b.getSouth()) * .1;
      const bbox = [b.getWest() - dx, b.getSouth() - dy, b.getEast() + dx, b.getNorth() + dy].map(v => v.toFixed(6)).join(',');
      try {
        windows = (await getJSON('/api/england/cells?' + new URLSearchParams({layer: `${source}-${metric}`, bbox, release: manifest.release_id}), 10000)).windows;
      } catch { windows = []; }
      if (ticket !== cellTicket) return;
    }
    const ids = new Set(windows.map(w => 'cells-' + w.id));
    for (const id of cellLayers) if (!ids.has(id)) { map.removeLayer(id); map.removeSource(id); }
    cellLayers = cellLayers.filter(id => ids.has(id));
    for (const w of windows) {
      const id = 'cells-' + w.id;
      if (cellLayers.includes(id)) continue;
      map.addSource(id, {type: 'image', url: `${w.url}?theme=${theme}`, coordinates: w.corners});
      map.addLayer({id, type: 'raster', source: id, paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 0}}, firstSymbol);
      cellLayers.push(id);
    }
    refreshCells();
  }
  function updateLayer() {
    drawLegend();
    if (ready) {
      loading(`Loading ${SOURCES[source].toLowerCase()} noise…`);
      map.getSource('noise').setTiles([tileUrl()]);
      syncCells();
    }
    drawSpot(); saveView();
  }
  function choose(newSource, newMetric) {
    source = newSource; metric = newMetric;
    document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
    document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
    updateLayer();
  }

  // ---------- saved places and recorded samples on the map ----------
  function drawSavedMarkers(list) {
    savedMarkers.forEach(m => m.remove()); savedMarkers = [];
    if (!map) return;
    list.forEach((p, i) => {
      const el = document.createElement('button'); el.type = 'button'; el.className = 'saved-marker'; el.textContent = String.fromCharCode(65 + i);
      el.setAttribute('aria-label', `Saved place ${String.fromCharCode(65 + i)}: ${p.label}`);
      el.addEventListener('click', event => { event.stopPropagation(); inspect([p.longitude, p.latitude], p.label); });
      savedMarkers.push(new maplibregl.Marker({element: el}).setLngLat([p.longitude, p.latitude]).addTo(map));
    });
  }
  function flyTo(coords, zoom) {
    const options = {center: coords, zoom: Math.max(map.getZoom(), zoom), offset: [0, phone.matches ? 40 : 90]};
    if (reduceMotion.matches) map.jumpTo(options); else map.flyTo({...options, duration: 1400, essential: false});
  }
  async function loadRecorded() {
    if (recorded) return recorded;
    $('recorded-status').textContent = 'Loading recorded samples…';
    const data = await getJSON('/api/observations', 10000);
    if (data.manifest.release_id !== app.observations_release) throw new Error('release_mismatch');
    recorded = data;
    $('recorded-count').textContent = `· ${data.locations.features.length} sites`;
    $('recorded-status').textContent = `${data.manifest.qa.accepted_recordings} short recordings in London. Ranges across recordings, not modelled annual averages.`;
    return recorded;
  }
  async function drawRecorded() {
    recordedMarkers.forEach(m => m.remove()); recordedMarkers = [];
    if (!$('recorded-toggle').checked) { saveView(); return; }
    try { await loadRecorded(); } catch { $('recorded-status').textContent = 'Recorded samples couldn’t be loaded.'; $('recorded-toggle').checked = false; return; }
    for (const item of recorded.locations.features) {
      const p = item.properties, el = document.createElement('button');
      el.type = 'button'; el.className = 'recorded-marker'; el.textContent = 'R'; el.setAttribute('aria-label', `Recorded sound site: ${p.name}`);
      el.addEventListener('click', event => {
        event.stopPropagation();
        const root = document.createElement('div'), title = document.createElement('p'), value = document.createElement('p'), detail = document.createElement('p');
        title.className = 'popup-title'; title.textContent = `${p.name} · recorded`;
        value.className = 'popup-value'; value.textContent = `${p.sample_min_db_a.toFixed(1)}–${p.sample_max_db_a.toFixed(1)}`;
        const unit = document.createElement('small'); unit.textContent = ' dB(A)'; value.append(unit);
        detail.className = 'popup-who';
        detail.textContent = `${p.sample_count} short recordings (${p.survey_years.join(', ')}), LAeq over ${p.duration_min_seconds.toFixed(0)}–${p.duration_max_seconds.toFixed(0)} s. A range across recordings, not uncertainty, and not comparable with yearly averages. Site position is approximate; calibration records not verified.`;
        const check = document.createElement('button'); check.type = 'button'; check.className = 'button'; check.textContent = 'Check modelled noise here';
        check.addEventListener('click', () => inspect(item.geometry.coordinates, p.name));
        const actions = document.createElement('div'); actions.className = 'popup-actions'; actions.append(check);
        root.append(title, value, detail, actions);
        if (!popup) popup = new maplibregl.Popup({closeButton: true, closeOnClick: false, maxWidth: '300px', offset: 16, focusAfterOpen: false});
        popup.setLngLat(item.geometry.coordinates).setDOMContent(root); if (!popup.isOpen()) popup.addTo(map);
      });
      recordedMarkers.push(new maplibregl.Marker({element: el}).setLngLat(item.geometry.coordinates).addTo(map));
    }
    saveView();
  }

  // ---------- search ----------
  async function search(event) {
    event.preventDefault();
    const ticket = ++navigation, query = $('place').value.trim(), list = $('search-results');
    list.replaceChildren(); list.hidden = true;
    // Latitude, longitude is read here and never sent to a search service.
    const coordinates = query.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (coordinates) {
      const lat = Number(coordinates[1]), lon = Number(coordinates[2]);
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) { $('search-status').textContent = 'Type latitude first (−90 to 90), then longitude (−180 to 180).'; return; }
      $('search-status').textContent = '';
      return go([lon, lat], `${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    }
    if (query.length < 2 || query.length > 160) { $('search-status').textContent = 'Type a place name or postcode (2–160 characters), or latitude, longitude.'; return; }
    $('search-status').textContent = 'Searching…';
    try {
      const data = await getJSON('/api/search?' + new URLSearchParams({q: query}), 20000);
      if (ticket !== navigation) return;
      if (!data.places.length) { $('search-status').textContent = 'No places found. Try a postcode, or type latitude, longitude.'; return; }
      const [best, ...others] = data.places;
      go([best.longitude, best.latitude], shortName(best.name));
      const status = $('search-status'); status.replaceChildren(`Showing ${shortName(best.name)}.`);
      if (others.length) {
        const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'text-button';
        toggle.textContent = `Not this one? ${others.length} other match${others.length > 1 ? 'es' : ''}`;
        toggle.setAttribute('aria-expanded', 'false');
        toggle.addEventListener('click', () => {
          list.hidden = !list.hidden; toggle.setAttribute('aria-expanded', String(!list.hidden));
          if (!list.hidden && phone.matches) sheet('open');
        });
        status.append(' ', toggle);
        for (const place of others) {
          const li = document.createElement('li'), button = document.createElement('button'), strong = document.createElement('strong'), rest = document.createElement('span');
          button.type = 'button'; strong.textContent = shortName(place.name); rest.textContent = place.name;
          button.append(strong, rest);
          button.addEventListener('click', () => {
            ++navigation; list.hidden = true; toggle.remove();
            status.firstChild.textContent = `Showing ${shortName(place.name)}.`;
            go([place.longitude, place.latitude], shortName(place.name));
          });
          li.append(button); list.append(li);
        }
      }
    } catch {
      if (ticket === navigation) $('search-status').textContent = 'Place search isn’t available right now. You can type latitude, longitude instead.';
    }
  }
  function go(coords, name) {
    if (phone.matches) { sheet('peek'); $('place').blur(); }
    if (ready) flyTo(coords, 15); else pendingFly = true;
    inspect(coords, name);
  }

  // ---------- phone sheet ----------
  function sheet(state) {
    const panel = $('panel');
    panel.dataset.state = state;
    $('sheet-handle').setAttribute('aria-expanded', String(state === 'open'));
    if (state === 'peek') $('panel-body').scrollTop = 0;
  }
  function setupSheet() {
    const panel = $('panel'), handle = $('sheet-handle');
    let startY = null, startOpen = false, moved = 0;
    handle.addEventListener('click', () => { if (moved < 6) sheet(panel.dataset.state === 'open' ? 'peek' : 'open'); });
    handle.addEventListener('pointerdown', event => {
      if (!phone.matches) return;
      startY = event.clientY; startOpen = panel.dataset.state === 'open'; moved = 0;
      handle.setPointerCapture(event.pointerId); panel.classList.add('dragging');
    });
    handle.addEventListener('pointermove', event => {
      if (startY === null) return;
      const height = panel.offsetHeight, peek = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--peek')) || 148;
      const base = startOpen ? 0 : height - peek, dy = event.clientY - startY;
      moved = Math.max(moved, Math.abs(dy));
      panel.style.transform = `translateY(${Math.min(height - peek, Math.max(0, base + dy))}px)`;
    });
    const end = event => {
      if (startY === null) return;
      const dy = event.clientY - startY;
      panel.classList.remove('dragging'); panel.style.transform = ''; startY = null;
      if (moved >= 6) sheet(dy < -40 ? 'open' : dy > 40 ? 'peek' : (startOpen ? 'open' : 'peek'));
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
    phone.addEventListener('change', () => { sheet('peek'); map?.resize(); });
    $('legend-change').addEventListener('click', () => {
      sheet('open');
      $('sources').scrollIntoView({block: 'start', behavior: reduceMotion.matches ? 'auto' : 'smooth'});
      $('sources').querySelector('input:checked').focus({preventScroll: true});
    });
  }

  // ---------- first-visit tip ----------
  function showTip() {
    if (storage.get('quietuk-tip-seen') || point) return;
    $('tip').hidden = false;
  }
  function hideTip() {
    if ($('tip').hidden) return;
    $('tip').hidden = true; storage.set('quietuk-tip-seen', '1');
  }

  // ---------- start ----------
  async function basemap() {
    try {
      const response = await fetch(BASEMAP[theme], {signal: AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error();
      return await response.json();
    } catch {
      message('The background map isn’t available, so streets and place names are missing. The noise data still works.');
      return {version: 8, sources: {}, layers: [{id: 'background', type: 'background', paint: {'background-color': theme === 'dark' ? '#0c0c0c' : '#eef0ea'}}]};
    }
  }
  function padding() {
    const area = document.querySelector('.map-area').getBoundingClientRect(), legend = $('legend').getBoundingClientRect();
    return phone.matches ? {top: legend.bottom - area.top + 12, bottom: 16, left: 16, right: 16}
      : {top: 40, bottom: Math.max(40, area.bottom - legend.top + 16), left: 40, right: 60};
  }
  function addLayers() {
    firstSymbol = map.getStyle().layers.find(l => l.type === 'symbol')?.id;
    map.addSource('noise', {type: 'raster', tiles: [tileUrl()], tileSize: manifest.tiles.tile_size,
      minzoom: manifest.tiles.zooms[0], maxzoom: manifest.tiles.zooms[1], bounds: [-7, 49.8, 2, 56], attribution: 'Noise: Defra, OGL v3'});
    map.addLayer({id: 'noise', type: 'raster', source: 'noise',
      layout: {visibility: $('overlay').checked ? 'visible' : 'none'},
      paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 120}}, firstSymbol);
    map.addSource('selected-cell', {type: 'geojson', data: empty});
    map.addLayer({id: 'cell-outline', type: 'line', source: 'selected-cell', minzoom: 13,
      paint: {'line-color': theme === 'dark' ? '#f4f5f2' : '#1c2822', 'line-width': 2}});
  }
  function onReady() {
    if (ready) return;
    addLayers();
    ready = true;
    map.on('moveend', () => { syncCells(); saveView(); });
    map.on('sourcedata', event => {
      if (cellLayers.includes(event.sourceId)) refreshCells();
      if (event.sourceId === 'noise' && map.isSourceLoaded('noise')) loading(null);
    });
    map.on('idle', () => { loading(null); refreshCells(); });
    map.once('idle', showTip);
    map.on('click', event => { ++navigation; inspect([event.lngLat.lng, event.lngLat.lat]); });
    map.getCanvas().addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inspectCentre(); }
    });
    drawSavedMarkers(places.places());
    syncCells();
    if (pendingFly && point) flyTo(point, 15);
    // A shared or restored view can come from a wider screen: keep the chosen spot in sight.
    else if (point && !map.getBounds().contains(point)) map.jumpTo({center: point});
    pendingFly = false;
    showOnMap();
    if (hash.get('samples') === '1' && app?.observations_release) { $('recorded-toggle').checked = true; drawRecorded(); }
    setTimeout(() => loading(null), 20000);
  }
  function inspectCentre() { const c = map.getCenter(); ++navigation; inspect([c.lng, c.lat]); }
  async function share(statusId) {
    saveView();
    try { await navigator.clipboard.writeText(window.location.href); $(statusId).textContent = 'Link copied. It opens this view on this computer while Quiet UK is running.'; }
    catch { $(statusId).textContent = 'Copy the address from your browser’s address bar to share this view.'; }
  }

  async function start() {
    setupSheet();
    const style = basemap();
    const appInfo = getJSON('/api/app', 10000).catch(() => null);
    try {
      manifest = await getJSON('/api/england');
      look = manifest.presentation;
    } catch {
      loading(null);
      message('The England map couldn’t be loaded. Close this tab and start Quiet UK again with the launcher.');
      return;
    }
    if (Object.hasOwn(SOURCES, hash.get('source'))) source = hash.get('source');
    if (METRICS.includes(hash.get('metric'))) metric = hash.get('metric');
    $('overlay').checked = hash.get('noise') !== '0';
    document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
    document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
    drawLegend(); drawHelpScale(); drawResearch();
    places = window.createEnglandPlaces({
      manifest, describe,
      onShow: p => { if (phone.matches) sheet('peek'); if (ready) flyTo([p.longitude, p.latitude], 15); inspect([p.longitude, p.latitude], p.label); },
      onChange: list => drawSavedMarkers(list),
      initialHash: hash,
    });
    if (hash.has('v') && hash.get('v') !== manifest.release_id) message('This link was made with an older version of the map data, so the current data is shown.');

    // Controls work straight away; a spot chosen before the map is ready is checked once it is.
    $('sources').addEventListener('change', event => choose(event.target.value, metric));
    $('metrics').addEventListener('change', event => choose(source, event.target.value));
    $('overlay').addEventListener('change', () => {
      if (ready) map.setLayoutProperty('noise', 'visibility', $('overlay').checked ? 'visible' : 'none');
      updateLayer();
    });
    $('search').addEventListener('submit', search);
    $('place').addEventListener('focus', () => { if (phone.matches && !$('search-results').hidden) sheet('open'); });
    $('inspect-centre').addEventListener('click', () => ready && inspectCentre());
    $('inspect-centre-panel').addEventListener('click', () => { if (!ready) return; if (phone.matches) sheet('peek'); inspectCentre(); });
    $('share-view').addEventListener('click', () => share('share-status'));
    $('share-view-panel').addEventListener('click', () => share('share-status'));
    $('dismiss-tip').addEventListener('click', hideTip);
    for (const b of document.querySelectorAll('[data-open-help]')) b.addEventListener('click', () => $('help-dialog').showModal());
    $('open-help').addEventListener('click', () => $('help-dialog').showModal());
    for (const dialog of document.querySelectorAll('dialog')) {
      dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
      dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    }
    $('recorded-toggle').addEventListener('change', drawRecorded);

    app = await appInfo;
    if (app?.observations_release) $('recorded-row').hidden = false;

    const camera = hash.get('camera')?.split(',').map(Number);
    const saved = camera?.length === 3 && camera.every(Number.isFinite) && Math.abs(camera[0]) <= 180 && Math.abs(camera[1]) <= 85;
    map = new maplibregl.Map({
      container: 'map', style: await style, minZoom: 5, maxZoom: 19, dragRotate: false, touchPitch: false, pitchWithRotate: false,
      attributionControl: {compact: true},
      ...(saved ? {center: camera.slice(0, 2), zoom: camera[2]} : {bounds: ENGLAND, fitBoundsOptions: {padding: padding()}}),
    });
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
    map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-right');
    map.getCanvas().setAttribute('aria-label', 'Noise map. Arrow keys move, plus and minus zoom, Enter checks the spot at the centre.');
    // The dark basemap names a few icons its sprite lacks; a blank stands in so they fail quietly.
    map.on('styleimagemissing', event => { if (!map.hasImage(event.id)) map.addImage(event.id, {width: 1, height: 1, data: new Uint8Array(4)}); });
    map.on('style.load', onReady);
    map.on('load', onReady);
    if (map.isStyleLoaded()) onReady();

    const savedPoint = hash.get('point')?.split(',').map(Number);
    if (savedPoint?.length === 2 && savedPoint.every(Number.isFinite) && Math.abs(savedPoint[0]) <= 180 && Math.abs(savedPoint[1]) <= 90) {
      const label = hash.get('label');
      inspect(savedPoint, label && label.length <= 60 && !/[\x00-\x1f\x7f]/.test(label) ? label : null);
    }
  }
  // Pasting another view link into this tab changes only the fragment; reload to apply it.
  // Our own replaceState updates do not fire hashchange.
  window.addEventListener('hashchange', () => window.location.reload());
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => window.location.reload());
  start();
})();
