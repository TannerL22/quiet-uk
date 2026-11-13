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
  // How each look treats the background map. Only colours and label order change; the noise
  // colours themselves come from the server (map_presentation.py), so every look shows the same classes.
  const LOOKS = {
    night: {basemap: 'dark', background: '#0b0a10', water: '#111726', park: '#11131a', residential: '#13111b', wood: '#10131a',
            building: '#17141f', text: '#a8a0b8', textMajor: '#e8e2f2', halo: 'rgba(11,10,16,0.92)', outline: 'rgba(240,234,247,0.2)',
            cell: '#fdd57a', border: 'rgba(232,226,242,0.62)', glow: true},
    atlas: {basemap: 'light', background: '#f3ede2', water: '#d6dedb', park: '#e8e2d0', residential: '#ede5d6', wood: '#e5dfcb',
            building: '#e4dac8', text: '#5b4b3e', textMajor: '#2b211b', halo: 'rgba(243,237,226,0.95)', outline: 'rgba(43,33,27,0.3)',
            cell: '#2b211b', border: '#7a6b5e'},
    guide: {basemap: 'light', background: '#f7f8f6', water: '#d2e4ec', park: '#e6efe8', text: '#4a575d', textMajor: '#1d2a2b',
            halo: 'rgba(255,255,255,0.95)', outline: 'rgba(29,42,43,0.26)', cell: '#1d2a2b', border: '#657373'},
  };
  const LOOK_NAMES = Object.keys(LOOKS);
  const ENGLAND = [[-6.0, 49.9], [1.8, 55.82]];
  const BOUNDS = [-7, 49.8, 2, 56];
  // Close up, exact native-cell images replace the zoom-14 tiles, which can
  // shift a cell edge by up to one tile pixel (about 6 m).
  const CELL_ZOOM = 15.5;
  // Below this map zoom the averaged tiles are blended when enlarged; closer in every square stays crisp.
  const SMOOTH_BELOW = 12;
  // The sea is painted over the colours up to here, so coastlines read cleanly; closer in nothing is covered.
  const MASK_UNTIL = 12;
  // Zoomed out, a light wash over everything outside England (from the ONS boundary) fades out by here;
  // the dashed border with Wales and Scotland stays at every zoom.
  const WASH_UNTIL = 11.5;
  const empty = {type: 'FeatureCollection', features: []};
  const phone = window.matchMedia('(max-width: 760px)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const storage = {
    get: key => { try { return localStorage.getItem(key); } catch { return null; } },
    set: (key, value) => { try { localStorage.setItem(key, value); } catch { /* Private browsing: nothing to keep. */ } },
  };

  let manifest, look, app, map, places, spotMarker;
  let lookName = LOOK_NAMES.includes(document.documentElement.dataset.look) ? document.documentElement.dataset.look : 'night';
  let spot = null, point = null, placeName = null, nearName = null, spotError = null, recordedSite = null;
  let source = 'road', metric = 'Lden', selection = 0, navigation = 0;
  let cellLayers = [], cellTicket = 0, ready = false, pendingFly = false, firstAbove, lastFocus = null;
  let savedMarkers = [], recorded = null, recordedMarkers = [];
  const hexes = new Map();
  let hexTicket = 0, hoveredHex = null;
  let area = null, areaTicket = 0, drawing = null, areaMarkers = [], quietClicksUntil = 0;
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
  const layerName = () => `${source}-${metric}`;
  const overviewId = () => manifest.overview?.release_id || 'native';
  // The boundary that decides which no-data pixels are drawn is in every tile address, so a browser
  // never mixes tiles cached before and after it changed.
  const outlineId = () => manifest.outline ? manifest.outline.sha256.slice(0, 12) : 'none';
  const tileUrl = () => `/england-display/${manifest.release_id}/${overviewId()}/${layerName()}/{z}/{x}/{y}.png?theme=${lookName}&outline=${outlineId()}`;
  const cutoff = (s, m) => manifest.evidence_contract.zero_rule_sources.includes(s) ? (m === 'Lnight' ? 35 : 40) : null;
  const whoLimit = (s, m) => look.who_2018[s]?.[m] ?? null;
  // The colours of the current look for one layer (the Calm guide's depend on the layer's WHO value).
  function coloursFor(s = source, m = metric) {
    const t = look.themes[lookName];
    return t.by_layer ? t.by_layer[`${s}-${m}`] : t;
  }
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
  // A clicked spot is named after the nearest postcode or settlement when the place list is installed;
  // otherwise its position tells saved spots apart.
  const spotLabel = () => placeName || nearName || `Spot at ${spot.latitude.toFixed(4)}, ${spot.longitude.toFixed(4)}`;
  const plainSource = s => s === 'road' ? 'road traffic' : s === 'rail' ? 'trains' : 'aircraft';
  const lensAvailable = () => !!manifest?.overview && source !== 'aircraft';
  const lensOn = () => lensAvailable() && $('lens').checked;
  const noiseVisible = () => $('overlay').checked && !lensOn();
  // MapLibre draws a 256 px raster source at map zoom Z from tiles of zoom round(Z + 1).
  const tileZoom = () => Math.max(manifest.tiles.zooms[0], Math.min(manifest.tiles.zooms[1], Math.round(map.getZoom() + 1)));
  const averaged = () => !!manifest.overview && tileZoom() <= manifest.overview.tiles.zooms[1];

  // Everything the page says about one observation, from its evidence status.
  function describe(o) {
    if (o.value_db !== null) {
      // Night words sit 10 dB lower: the same weight the 24-hour measure gives night noise.
      const word = wordFor(o.metric === 'Lnight' ? o.value_db + 10 : o.value_db);
      return {kind: 'value', value: o.value_db.toFixed(1), unit: 'dB', short: o.value_db.toFixed(1), word,
              colour: coloursFor(o.source, o.metric).bands[bandIndex(o.value_db)],
              explain: `${word}: a yearly average of ${o.value_db.toFixed(1)} dB from ${plainSource(o.source)}, as modelled by Defra for this 10 m square.`};
    }
    if (o.status === 'verified_below_cutoff') {
      const limit = o.bounds.upper_db;
      return {kind: 'below', value: `Under ${limit}`, unit: 'dB', short: `<${limit}`, word: 'Very quiet', colour: coloursFor(o.source, o.metric).below,
              explain: `Very quiet for ${plainSource(o.source)}: Defra's model worked out a level under ${limit} dB here, but doesn't publish the exact number.`};
    }
    if (o.status === 'outside_extract') {
      return {kind: 'none', value: 'Not mapped', unit: '', short: '—', word: '',
              explain: 'This spot is outside the mapped area. The map covers England only.'};
    }
    const country = spot?.country?.name;
    if (country === 'Wales' || country === 'Scotland') {
      return {kind: 'none', value: 'Not mapped', unit: '', short: '—', word: '',
              explain: `This spot is in ${country}. Defra’s noise maps cover England only, so there’s no value here yet.`};
    }
    if (o.source === 'aircraft') {
      return {kind: 'none', value: 'Not mapped', unit: '', short: '—', word: '',
              explain: 'Aircraft noise is only mapped around major airports, and Defra publishes no value here. That doesn’t mean it’s quiet.'};
    }
    return {kind: 'none', value: 'No data', unit: '', short: '—', word: '',
            explain: 'Defra publishes no value for this square. That doesn’t mean it’s quiet.'};
  }
  function guideline(o) {
    const limit = whoLimit(o.source, o.metric);
    if (!limit) return '';
    const name = `the WHO's recommended limit for ${o.source} noise (${limit} dB)`;
    if (o.value_db !== null) return o.value_db > limit ? `Above ${name}.` : `Below ${name}.`;
    if (o.status === 'verified_below_cutoff' && o.bounds.upper_db <= limit) return `Below ${name}.`;
    return '';
  }
  const active = () => spot?.observations.find(o => o.source === source && o.metric === metric);

  // ---------- view state in the address ----------
  function saveView() {
    if (!manifest) return;
    const params = new URLSearchParams({source, metric, look: lookName, v: manifest.release_id, noise: $('overlay').checked ? '1' : '0'});
    if (lensOn()) params.set('lens', '1');
    if (map) { const c = map.getCenter(); params.set('camera', [c.lng.toFixed(6), c.lat.toFixed(6), map.getZoom().toFixed(2)].join(',')); }
    if (point) params.set('point', point.map(v => v.toFixed(6)).join(','));
    if (point && placeName) params.set('label', placeName);
    if (recorded && $('recorded-toggle').checked) params.set('samples', '1');
    if (area) {
      params.set('area', area.box.map(v => v.toFixed(5)).join(','));
      if (area.level !== null) params.set('level', String(area.level));
    }
    history.replaceState(null, '', '#' + params.toString());
  }

  // ---------- legend, notes and the help guide ----------
  function legendSteps() {
    const p = coloursFor(), limit = cutoff(source, metric), steps = [];
    p.bands.forEach((colour, i) => {
      if (i === 0 && limit === 40) return; // Road and rail daytime levels start at 40.
      const low = i === 0 ? (limit === 35 ? 35 : null) : look.breaks_db[i - 1], high = look.breaks_db[i] ?? null;
      steps.push({colour, low, high,
                  word: i === 0 ? (limit === 35 ? '35–40 dB' : 'Under 40 dB') : high === null ? '70 dB and over' : `${low}–${high} dB`});
    });
    return {below: limit === null ? null : {colour: p.below, label: `<${limit}`, word: `Under ${limit} dB: very quiet, exact level not published`}, steps};
  }
  function hatch(colour) { return `repeating-linear-gradient(45deg, ${colour} 0 2px, transparent 2px 5px)`; }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  // One scale: an optional "under the cutoff" swatch, a strip of equal steps, ticks at their edges, and a guideline mark.
  function drawScale({below, steps, ticks, mark, words, label}) {
    const scale = $('legend-scale'); scale.replaceChildren();
    scale.setAttribute('aria-label', label);
    const row = el('div', 'scale');
    if (below) {
      const b = el('div', 'scale-below'); b.title = below.word;
      const i = el('i'); i.style.background = below.colour; b.append(i, el('span', 'tick-end', below.label)); row.append(b);
    }
    const bands = el('div', 'scale-bands'), strip = el('div', 'scale-strip');
    for (const step of steps) { const i = el('i'); i.style.background = step.colour; i.title = step.word; strip.append(i); }
    bands.append(strip);
    for (const [at, text] of ticks) { const t = el('span', at === 0 ? 'tick start' : 'tick', text); t.style.left = `${at * 100}%`; bands.append(t); }
    if (mark) {
      const m = el('div', 'who-mark'); m.style.left = `${mark.at * 100}%`; m.title = mark.title;
      m.append(el('span', '', mark.text)); bands.append(m);
    }
    row.append(bands); scale.append(row);
    const w = el('div', 'legend-words'); w.append(el('span', '', words[0]), el('span', '', words[1])); scale.append(w);
  }
  function drawLegend() {
    const extra = $('legend-extra'); extra.replaceChildren();
    const item = (background, text) => {
      const span = el('span'), i = el('i'); i.style.background = background; span.append(i, text); extra.append(span);
    };
    if (lensOn()) {
      const colours = look.themes[lookName].lens, limit = cutoff(source, metric);
      $('legend-title').textContent = `Quiet areas · ${SOURCES[source].toLowerCase()} noise, ${TIME_SHORT[metric].toLowerCase()}`;
      drawScale({steps: colours.map((colour, i) => ({colour, word: `${i * 20}–${i * 20 + 20}% of the land very quiet`})),
                 ticks: [[.2, '20%'], [.4, '40%'], [.6, '60%'], [.8, '80%']], words: ['Less quiet', 'More quiet'],
                 label: `Share of land under ${limit} dB in each hexagon, from under 20% to over 80%`});
      extra.append(el('span', 'note', `Hexagons about 5 km across · share of each one's land under ${limit} dB`));
    } else {
      $('legend-title').textContent = `${SOURCES[source]} noise · ${TIME[metric]}`;
      const {below, steps} = legendSteps(), n = steps.length;
      const ticks = steps.map((s, i) => [i / n, s.low === null ? null : String(s.low)]).filter(([, t]) => t !== null);
      const limit = whoLimit(source, metric);
      let mark = null;
      if (limit !== null) {
        const j = steps.findIndex(s => (s.low === null || limit >= s.low) && (s.high === null || limit < s.high));
        const s = steps[j], frac = s.low === null || s.high === null ? 0 : (limit - s.low) / (s.high - s.low);
        mark = {at: (j + frac) / n, text: `WHO ${limit}`, title: `WHO 2018 guideline for ${source} noise: ${limit} dB ${metric}`};
      }
      drawScale({below, steps, ticks, mark, words: ['Quieter', 'Louder'],
                 label: `Colour scale from ${below ? below.word : steps[0].word} to ${steps[n - 1].word}` + (mark ? `. ${mark.title}.` : '.')});
      item(hatch(coloursFor().no_data), source === 'aircraft' ? 'Not mapped (aircraft: airports only)' : 'No published value');
      if (limit === null && source !== 'aircraft') extra.append(el('span', 'note', 'The WHO gives no daytime-only guideline'));
    }
    $('metrics').title = NOTES[metric];
    $('lens-row').hidden = !manifest.overview;
    $('lens').disabled = !lensAvailable();
    $('lens-row').title = source === 'aircraft' ? 'Not available for aircraft, which is only mapped around airports' : '';
    drawScaleNote();
  }
  function drawScaleNote() {
    const note = $('legend-scale-note');
    if (!map || !manifest) { note.textContent = ''; return; }
    note.textContent = lensOn() ? 'Hexagons ~5 km'
      : averaged() ? 'Area averages' : map.getZoom() >= CELL_ZOOM ? 'Exact 10 m squares'
      : !manifest.overview && map.getZoom() < 13.5 ? 'Sampled squares' : 'Every 10 m square';
  }
  function drawHelpScale() {
    const body = $('help-scale').tBodies[0]; body.replaceChildren();
    const p = coloursFor();
    const rows = [[p.below, 'Under 40 dB (under 35 at night)', 'Very quiet for transport noise. Exact level not published.']];
    p.bands.slice(1).forEach((colour, i) => {
      const low = look.breaks_db[i], high = look.breaks_db[i + 1];
      rows.push([colour, high ? `${low}–${high} dB` : `${low} dB and over`, wordFor(low)]);
    });
    rows.push([hatch(p.no_data), 'Grey stripes', 'No published value. Not the same as quiet.']);
    for (const [colour, range, word] of rows) {
      const tr = el('tr'), a = el('td'), b = el('td', '', range), c = el('td', '', word), i = el('i');
      i.style.background = colour; a.append(i); tr.append(a, b, c); body.append(tr);
    }
  }
  function overlayStatus(exact) {
    const status = $('overlay-status');
    if (lensOn()) { status.textContent = 'Each hexagon counts every 10 m square inside it.'; return; }
    if (!$('overlay').checked) { status.textContent = 'Noise colours hidden.'; return; }
    status.textContent = exact ? ''
      : averaged() ? 'Each colour averages the 10 m squares beneath it.'
      : !manifest.overview && map.getZoom() < 13.5 ? 'Zoomed out, colours are a sample of the squares.'
      : cellMode() ? 'Loading the exact 10 m squares…' : '';
  }

  // ---------- research section ----------
  function definition(list, term, ...values) {
    const dt = el('dt', '', term), dd = el('dd'); dd.append(...values); list.append(dt, dd);
  }
  function link(href, text) { const a = el('a', '', text); a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a; }
  const mono = text => el('span', 'mono', text);
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
    if (manifest.places) {
      const pl = manifest.places;
      definition(data, 'Place search', `${pl.name} (${pl.publisher}), version ${pl.version}: ${pl.rows.toLocaleString()} names, searched on this computer. Names only move the map: every noise value comes from the 10 m cell you check. `, mono(pl.release_id));
    }
    definition(data, 'Overview', manifest.overview ? mono(manifest.overview.release_id)
      : document.createTextNode(manifest.overview_problem ? `Not used: ${manifest.overview_problem}` : 'Not installed: zoomed-out tiles are samples.'));
    definition(data, 'Evidence contract', `Version ${manifest.evidence_contract.version}`);
    definition(data, 'Licence', link(manifest.licence_url, manifest.licence), document.createTextNode('. ' + manifest.attribution));
    $('research-policy').textContent = manifest.evidence_contract.missing_value_policy + ' ' + manifest.uncertainty;
    const drawing = $('research-drawing'); drawing.replaceChildren();
    const t = manifest.transformation, ostn15 = (t.grids || []).some(g => g.name.includes('OSTN15')), ov = manifest.overview;
    const items = ov ? [
      `Zoomed out (tile zooms ${ov.tiles.zooms[0]}–${ov.tiles.zooms[1]}): ${ov.method.tiles} These tiles are a display aid, not analytical data.`,
      `Closer in, the map's own tiles: ${manifest.display.method}. From zoom ${CELL_ZOOM} the original cells are drawn exactly, and clicking always reads the original cell.`,
      `Quiet areas: ${ov.method.hexes} A hexagon's share is of its calculated land cells; no-data cells are counted separately.`,
    ] : [
      `Zoomed out, ${manifest.display.method}, so thin roads can be skipped. From zoom ${CELL_ZOOM} the original cells are drawn exactly, and clicking always reads the original cell.`,
    ];
    if (manifest.outline) items.push(
      `Outside England (${manifest.outline.publisher}, ${manifest.outline.name}; ${manifest.outline.generalisation}), squares with no published value are left undrawn, so the edges of Defra’s 10 km squares don’t show. Values Defra did publish there are still drawn. Each pixel is tested at its centre on the National Grid, with the map’s own transformation. A dashed line marks England’s borders with Wales and Scotland, and below zoom ${WASH_UNTIL} a light wash covers everything outside England. Stored tiles, downloads and clicks are unchanged.`);
    items.push(
      `Below zoom ${MASK_UNTIL} the sea from OpenStreetMap is painted over the colours so coastlines read cleanly. The data under it is unchanged, and clicking still reads it.`,
      `Map positions use ${ostn15 ? "Ordnance Survey's OSTN15 grid" : t.description} (stated accuracy ${t.stated_accuracy_m} m).` + (t.better_operations_not_installed.length ? ' A more accurate grid is available but not installed.' : ''),
      `Looks (${look.version}): Night glow, a dark scale from purple to pale gold with a soft blurred copy underneath when zoomed out; Editorial atlas, sand to oxblood ink; Calm guide, split at the WHO 2018 guideline value for the layer. Each scale gets steadily lighter or darker from quiet to loud. ${look.note}`,
      'Hovering shows the class of the tile pixel under the pointer, read from the same tiles. Clicking reads the original cell.',
    );
    for (const text of items) drawing.append(el('li', '', text));
    const summaries = $('research-area'); summaries.replaceChildren();
    const areaInfo = manifest.area;
    if (areaInfo) {
      const am = areaInfo.method;
      for (const text of [`Boxes up to ${areaInfo.max_km2} km² and ${areaInfo.max_side_km} km across, for road and rail. ${am.cells}`, am.shares, am.median, am.energy_average,
                          am.loudest, `${am.quietest} The level is chosen on the page (default: the WHO 2018 guideline, or ${areaInfo.default_level_db} dB in the daytime).`,
                          am.analytical_use, 'The download holds every count, both places with their cells and records, and the method.']) {
        summaries.append(el('li', '', text));
      }
    }
    $('credits-places').hidden = !manifest.places;
  }
  function drawResearchSpot() {
    const list = $('research-spot'); list.replaceChildren();
    const o = active();
    $('research-spot-empty').hidden = !!spot; list.hidden = !spot; $('point-download').hidden = !spot;
    if (!spot) return;
    definition(list, 'Latitude, longitude', mono(`${spot.latitude.toFixed(6)}, ${spot.longitude.toFixed(6)}`));
    if (spot.easting !== null) definition(list, 'British National Grid', mono(`${spot.easting.toFixed(1)} E, ${spot.northing.toFixed(1)} N`));
    definition(list, 'Defra square', mono(spot.tile || 'none'));
    if (spot.country) definition(list, 'Country (ONS boundary)', spot.country.name || 'None: the sea, or outside Great Britain');
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

  // ---------- the selected spot: the card on the map, and the full table in the drawer ----------
  function readingNodes(d) {
    const value = el('span', '', d.value);
    if (d.unit) value.append(el('small', '', ' ' + d.unit));
    const chip = el('span', 'level-chip');
    if (d.word) { const i = el('i'); i.style.background = d.colour; chip.append(i, d.word); }
    return [value, chip];
  }
  function showResult(shown) {
    $('result').hidden = !shown;
    document.body.classList.toggle('has-result', shown);
  }
  function drawResult() {
    if (recordedSite) return drawRecordedResult();
    $('recorded-actions').hidden = true; $('result-actions').hidden = false;
    $('result-eyebrow').textContent = 'Selected spot';
    if (!point) { showResult(false); return; }
    showResult(true);
    if (!spot) {
      $('spot-title').textContent = placeName || 'Selected spot';
      $('spot-value').replaceChildren(el('span', 'checking', spotError ? '' : 'Checking…'));
      $('spot-level').replaceChildren(); $('spot-layer').textContent = '';
      $('spot-explain').textContent = spotError || ''; $('spot-who').textContent = ''; $('spot-area').hidden = true;
      $('result-actions').hidden = !!spotError;
      return;
    }
    const o = active(), d = describe(o);
    $('spot-title').textContent = spotLabel();
    const [value, chip] = readingNodes(d);
    $('spot-value').replaceChildren(...value.childNodes);
    $('spot-level').replaceChildren(...chip.childNodes);
    $('spot-layer').textContent = `${SOURCES[source]} noise · ${TIME[metric]}`;
    $('spot-explain').textContent = d.explain;
    $('spot-who').textContent = guideline(o);
    drawArea();
  }
  // With the quiet-area lens on, the card also says what the hexagon around the spot is like.
  function drawArea() {
    const note = $('spot-area');
    const feature = lensOn() && ready && point && map.getLayer('hex-fill')
      ? map.queryRenderedFeatures(map.project(point), {layers: ['hex-fill']})[0] : null;
    note.hidden = !feature;
    if (feature) note.textContent = hexSentence(feature.properties);
  }
  function hexSentence(p) {
    const limit = cutoff(source, metric), who = whoLimit(source, metric), pct = v => `${Math.round(v * 100)}%`;
    const parts = [`${pct(p.quiet)} of the land in this 5 km hexagon is under ${limit} dB from ${plainSource(source)}`];
    if (p.who !== null && p.who !== undefined && who !== null) parts.push(`${pct(p.who)} is at or over the WHO guideline (${who} dB)`);
    if (p.mean_db !== null && p.mean_db !== undefined) parts.push(`the area average is ${Number(p.mean_db).toFixed(1)} dB or more`);
    return parts.join('; ') + (p.nodata_km2 > 0 ? `. ${Number(p.nodata_km2).toFixed(1)} km² has no published value.` : '.');
  }
  function drawSpotDetail() {
    $('spot-empty').hidden = !!spot; $('spot').hidden = !spot;
    if (!spot) return;
    $('spot-detail-title').textContent = spotLabel();
    const body = $('readings'); body.replaceChildren();
    for (const key of Object.keys(SOURCES)) {
      const tr = el('tr'), th = el('th', '', SOURCES[key]);
      th.scope = 'row'; tr.append(th);
      for (const m of METRICS) {
        const ob = spot.observations.find(x => x.source === key && x.metric === m), od = describe(ob);
        const td = el('td'), button = el('button', '', od.short);
        button.type = 'button'; button.title = od.explain;
        button.setAttribute('aria-label', `${SOURCES[key]}, ${TIME_SHORT[m]}: ${od.value}${od.unit ? ' ' + od.unit : ''}. Show on map.`);
        button.setAttribute('aria-pressed', String(key === source && m === metric));
        if (ob.value_db === null) button.classList.add('missing');
        button.addEventListener('click', () => choose(key, m));
        td.append(button); tr.append(td);
      }
      body.append(tr);
    }
  }
  function drawSpot() {
    places?.update(spot ? {longitude: spot.longitude, latitude: spot.latitude, label: spotLabel()} : null, source, metric);
    drawResult(); drawSpotDetail(); drawResearchSpot();
    const o = spot && active();
    if (ready) map.getSource('selected-cell')?.setData(o?.cell ? {type: 'Feature', properties: {}, geometry: o.cell} : empty);
  }
  // The marker needs the map; the lookup itself does not, so it never waits for the basemap.
  function showOnMap() {
    if (!ready || !point) return;
    if (!spotMarker) spotMarker = new maplibregl.Marker({element: el('div', 'spot-marker')});
    spotMarker.setLngLat(point).addTo(map);
  }
  async function inspect(coords, name = null) {
    const current = ++selection;
    point = coords; placeName = name; nearName = null; spot = null; spotError = null; recordedSite = null;
    hideTip(); showOnMap(); drawSpot(); saveView();
    if (!name) nameSpot(current, coords);
    try {
      const data = await getJSON('/api/england/location?' + new URLSearchParams({lon: coords[0], lat: coords[1], release: manifest.release_id}));
      if (current !== selection) return;
      if (data.release_id !== manifest.release_id) throw new Error('release_mismatch');
      spot = data; drawSpot();
    } catch (error) {
      if (current !== selection) return;
      spot = null;
      spotError = error.message === 'release_mismatch'
        ? 'The map data has been updated. Reload the page to check this spot.'
        : 'This spot couldn’t be loaded or checked. Try again.';
      drawSpot();
    }
  }
  // Context only: the nearest postcode (within 400 m) or settlement (3 km) names the card.
  async function nameSpot(current, coords) {
    if (!placesAvailable()) return;
    try {
      const data = await getJSON('/api/places/near?' + new URLSearchParams({lon: coords[0].toFixed(6), lat: coords[1].toFixed(6)}), 5000);
      if (current !== selection || placeName || !data.label) return;
      nearName = data.label; drawSpot();
    } catch { /* The card keeps the spot's position as its name. */ }
  }
  function clearSpot() {
    ++selection; point = null; placeName = null; nearName = null; spot = null; spotError = null; recordedSite = null;
    spotMarker?.remove(); drawSpot(); saveView();
  }
  function openDetails() { openDrawer('spot'); }

  // ---------- map layers ----------
  const cellMode = () => noiseVisible() && map.getZoom() >= CELL_ZOOM;
  function setVisible(id, shown) {
    if (!map.getLayer(id)) return;
    const value = shown ? 'visible' : 'none';
    if (map.getLayoutProperty(id, 'visibility') !== value) map.setLayoutProperty(id, 'visibility', value);
  }
  function refreshCells() {
    if (!map.getLayer('noise')) return;
    const exact = cellMode() && cellLayers.length > 0 && cellLayers.every(id => map.isSourceLoaded(id));
    setVisible('noise', noiseVisible() && !exact);
    setVisible('noise-glow', noiseVisible() && LOOKS[lookName].glow);
    for (const id of cellLayers) setVisible(id, cellMode());
    for (const id of ['hex-fill', 'hex-line', 'hex-hover']) setVisible(id, lensOn());
    const smooth = map.getZoom() < SMOOTH_BELOW ? 'linear' : 'nearest';
    if (map.getPaintProperty('noise', 'raster-resampling') !== smooth) map.setPaintProperty('noise', 'raster-resampling', smooth);
    overlayStatus(exact); drawScaleNote();
  }
  async function syncCells() {
    const ticket = ++cellTicket;
    let windows = [];
    if (cellMode()) {
      const b = map.getBounds(), dx = (b.getEast() - b.getWest()) * .1, dy = (b.getNorth() - b.getSouth()) * .1;
      const bbox = [b.getWest() - dx, b.getSouth() - dy, b.getEast() + dx, b.getNorth() + dy].map(v => v.toFixed(6)).join(',');
      try {
        windows = (await getJSON('/api/england/cells?' + new URLSearchParams({layer: layerName(), bbox, release: manifest.release_id}), 10000)).windows;
      } catch { windows = []; }
      if (ticket !== cellTicket) return;
    }
    const ids = new Set(windows.map(w => 'cells-' + w.id));
    for (const id of cellLayers) if (!ids.has(id)) { if (map.getLayer(id)) map.removeLayer(id); if (map.getSource(id)) map.removeSource(id); }
    cellLayers = cellLayers.filter(id => ids.has(id));
    for (const w of windows) {
      const id = 'cells-' + w.id;
      if (cellLayers.includes(id)) continue;
      map.addSource(id, {type: 'image', url: `${w.url}?theme=${lookName}&outline=${outlineId()}`, coordinates: w.corners});
      map.addLayer({id, type: 'raster', source: id, paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 0}}, 'hex-fill');
      cellLayers.push(id);
    }
    refreshCells();
  }
  async function syncHexes() {
    const ticket = ++hexTicket;
    if (!ready || !map.getSource('hexes')) return;
    if (!lensOn()) { refreshCells(); drawArea(); return; }
    const layer = layerName();
    if (!hexes.has(layer)) {
      hexes.set(layer, getJSON('/api/england/hexes?' + new URLSearchParams({layer, release: manifest.release_id}), 20000)
        .catch(error => { hexes.delete(layer); throw error; }));
    }
    try {
      loading('Counting quiet areas…');
      const data = await hexes.get(layer);
      if (ticket !== hexTicket || !map.getSource('hexes')) return;
      map.getSource('hexes').setData(data);
      hoveredHex = null; map.setFilter('hex-hover', ['==', ['id'], -1]);
    } catch {
      if (ticket === hexTicket) { $('lens').checked = false; message('The quiet-area hexagons couldn’t be loaded.'); drawLegend(); }
    }
    loading(null); refreshCells();
    map.once('idle', drawArea);
  }
  function updateLayer() {
    drawLegend(); drawHelpScale();
    if (ready) {
      loading(`Loading ${SOURCES[source].toLowerCase()} noise…`);
      map.getSource('noise').setTiles([tileUrl()]);
      map.getSource('noise-glow')?.setTiles([tileUrl()]);
      for (const id of cellLayers) { map.removeLayer(id); map.removeSource(id); }
      cellLayers = [];
      syncCells(); syncHexes();
    }
    clearHover(); drawSpot(); saveView();
    if (area) { area.level = null; summarise(); }  // The chosen level depends on the layer's cutoff and guideline.
  }
  function choose(newSource, newMetric) {
    source = newSource; metric = newMetric;
    document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
    document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
    updateLayer();
  }

  // ---------- the background map for each look ----------
  const basemaps = {};
  function basemap(kind) {
    basemaps[kind] ??= fetch(BASEMAP[kind], {signal: AbortSignal.timeout(8000)})
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .catch(() => {
        delete basemaps[kind];
        message('The background map isn’t available, so streets and place names are missing. The noise data still works.');
        return {version: 8, sources: {}, layers: [{id: 'background', type: 'background', paint: {'background-color': '#808080'}}]};
      });
    return basemaps[kind];
  }
  function restyle(layer, t) {
    const p = layer.paint = {...(layer.paint || {})};
    if (layer.type === 'background') p['background-color'] = t.background;
    else if (layer.id === 'water') p['fill-color'] = t.water;
    else if (layer.id === 'waterway') p['line-color'] = t.water;
    else if (layer.type === 'fill' && layer.id.includes('park')) p['fill-color'] = t.park;
    else if (layer.id === 'landuse_residential' && t.residential) p['fill-color'] = t.residential;
    else if (layer.id === 'landcover_wood' && t.wood) p['fill-color'] = t.wood;
    else if (layer.id === 'building' && t.building) { p['fill-color'] = t.building; delete p['fill-outline-color']; }
    else if (layer.type === 'symbol' && layer.layout?.['text-field'] && !layer.id.includes('shield')) {
      p['text-color'] = /city|country|state|town/.test(layer.id) ? t.textMajor : t.text;
      p['text-halo-color'] = t.halo;
    }
  }
  async function styleFor(name) {
    const t = LOOKS[name], style = structuredClone(await basemap(t.basemap));
    const symbols = [], others = [];
    for (const layer of style.layers) {
      if (layer.id.startsWith('road_oneway')) continue;  // Arrows would sit under the noise colours.
      restyle(layer, t);
      (layer.type === 'symbol' ? symbols : others).push(layer);
    }
    // Every label goes last, so the noise can sit over every road and under every name.
    style.layers = [...others, ...symbols];
    return style;
  }
  function addLayers() {
    if (map.getSource('noise')) return;
    const t = LOOKS[lookName], layers = map.getStyle().layers, vector = !!map.getSource('openmaptiles');
    firstAbove = layers.find(l => l.type === 'symbol' || l['source-layer'] === 'boundary')?.id;
    const tiles = {tiles: [tileUrl()], minzoom: manifest.tiles.zooms[0], maxzoom: manifest.tiles.zooms[1], bounds: BOUNDS};
    if (t.glow) {
      // The same tiles drawn four times larger from two zooms out: a soft halo around loud corridors.
      map.addSource('noise-glow', {type: 'raster', ...tiles, tileSize: manifest.tiles.tile_size * 4, maxzoom: Math.min(12, tiles.maxzoom)});
      map.addLayer({id: 'noise-glow', type: 'raster', source: 'noise-glow', maxzoom: 12.5, layout: {visibility: noiseVisible() ? 'visible' : 'none'},
        paint: {'raster-opacity': ['interpolate', ['linear'], ['zoom'], 5, .75, 9, .55, 12, 0], 'raster-resampling': 'linear', 'raster-fade-duration': 0}}, firstAbove);
    }
    map.addSource('noise', {type: 'raster', ...tiles, tileSize: manifest.tiles.tile_size, attribution: 'Noise: Defra, OGL v3'});
    map.addLayer({id: 'noise', type: 'raster', source: 'noise', layout: {visibility: noiseVisible() ? 'visible' : 'none'},
      paint: {'raster-opacity': ['interpolate', ['linear'], ['zoom'], 12, .94, 17, .8],
              'raster-resampling': map.getZoom() < SMOOTH_BELOW ? 'linear' : 'nearest', 'raster-fade-duration': 150}}, firstAbove);
    const lens = look.themes[lookName].lens;
    map.addSource('hexes', {type: 'geojson', data: empty});
    map.addLayer({id: 'hex-fill', type: 'fill', source: 'hexes', layout: {visibility: 'none'},
      paint: {'fill-color': ['step', ['coalesce', ['get', 'quiet'], 0], lens[0], .2, lens[1], .4, lens[2], .6, lens[3], .8, lens[4]], 'fill-opacity': .9}}, firstAbove);
    map.addLayer({id: 'hex-line', type: 'line', source: 'hexes', layout: {visibility: 'none'},
      paint: {'line-color': t.background, 'line-width': ['interpolate', ['linear'], ['zoom'], 6, .4, 10, 1.5]}}, firstAbove);
    map.addLayer({id: 'hex-hover', type: 'line', source: 'hexes', layout: {visibility: 'none'}, filter: ['==', ['id'], -1],
      paint: {'line-color': t.cell, 'line-width': 2.5}}, firstAbove);
    if (vector) {
      map.addLayer({id: 'ocean-mask', type: 'fill', source: 'openmaptiles', 'source-layer': 'water', maxzoom: MASK_UNTIL,
        filter: ['==', ['get', 'class'], 'ocean'], paint: {'fill-color': t.water, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 10.5, 1, MASK_UNTIL, 0]}}, firstAbove);
    }
    if (manifest.outline) {
      // Where the map ends: England's borders with Wales and Scotland, and a wash over everything else.
      map.addSource('outline', {type: 'geojson', data: `/api/england/outline?outline=${outlineId()}`, attribution: 'Boundaries: ONS, OGL v3'});
      map.addLayer({id: 'outside-wash', type: 'fill', source: 'outline', maxzoom: WASH_UNTIL, filter: ['==', ['get', 'kind'], 'outside'],
        // Without antialiasing, the edges the wash's pieces share don't show as faint lines.
        paint: {'fill-antialias': false, 'fill-color': t.background, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, .55, 9, .5, 10.5, .3, WASH_UNTIL, 0]}}, firstAbove);
      map.addLayer({id: 'england-border', type: 'line', source: 'outline', filter: ['==', ['get', 'kind'], 'border'], layout: {'line-join': 'round'},
        paint: {'line-color': t.border, 'line-dasharray': [3, 2], 'line-width': ['interpolate', ['linear'], ['zoom'], 5, .8, 10, 1.3, 15, 2]}}, firstAbove);
    }
    if (vector) {
      // Close in, building outlines over the colours help to tell one side of a street from the other.
      map.addLayer({id: 'building-outline', type: 'line', source: 'openmaptiles', 'source-layer': 'building', minzoom: 14.5,
        paint: {'line-color': t.outline, 'line-width': ['interpolate', ['linear'], ['zoom'], 14.5, .3, 18, 1]}}, firstAbove);
    }
    map.addSource('selected-cell', {type: 'geojson', data: empty});
    map.addLayer({id: 'cell-outline', type: 'line', source: 'selected-cell', minzoom: 13, paint: {'line-color': t.cell, 'line-width': 2}}, firstAbove);
    // A selected area: its box, the quietest and loudest cells, and a line from the quietest to what limits it.
    map.addSource('area-box', {type: 'geojson', data: empty});
    map.addLayer({id: 'area-fill', type: 'fill', source: 'area-box', paint: {'fill-color': ['case', ['get', 'warn'], '#c2413b', t.cell], 'fill-opacity': .06}}, firstAbove);
    map.addLayer({id: 'area-line', type: 'line', source: 'area-box', layout: {'line-join': 'round'},
      paint: {'line-color': ['case', ['get', 'warn'], '#c2413b', t.cell], 'line-width': 2, 'line-dasharray': [2.5, 1.5]}}, firstAbove);
    map.addSource('area-marks', {type: 'geojson', data: empty});
    map.addLayer({id: 'area-link', type: 'line', source: 'area-marks', filter: ['==', ['get', 'kind'], 'link'],
      paint: {'line-color': t.cell, 'line-width': 1.5, 'line-dasharray': [1, 2]}}, firstAbove);
    map.addLayer({id: 'area-cells', type: 'line', source: 'area-marks', minzoom: 13, filter: ['!=', ['get', 'kind'], 'link'],
      paint: {'line-color': t.cell, 'line-width': 2}}, firstAbove);
    cellLayers = [];
  }
  async function setLook(name, {save = true} = {}) {
    if (!LOOK_NAMES.includes(name)) return;
    const changed = name !== lookName;
    lookName = name;
    document.documentElement.dataset.look = name;
    document.querySelector(`input[name="look"][value="${name}"]`).checked = true;
    $('theme-color').content = LOOKS[name].background;
    if (save) storage.set('quietuk-look', name);
    if (!manifest) return;
    drawLegend(); drawHelpScale(); drawSpot(); saveView(); clearHover();
    if (map && changed) {
      loading('Changing the look…');
      const style = await styleFor(name);
      if (name !== lookName) return;
      ready = false;
      map.setStyle(style, {diff: false});
    }
  }

  // ---------- pointer readout ----------
  // The class under the pointer comes from the same tiles, served with a palette whose entry i is red i*20.
  const indexTiles = new Map();
  let hoverEvent = null, hoverFrame = 0, hoverTicket = 0;
  function indexTile(layer, z, x, y) {
    const url = `/england-display/${manifest.release_id}/${overviewId()}/${layer}/${z}/${x}/${y}.png?theme=index&outline=${outlineId()}`;
    if (!indexTiles.has(url)) {
      if (indexTiles.size > 80) indexTiles.delete(indexTiles.keys().next().value);
      indexTiles.set(url, fetch(url).then(r => r.status === 200 ? r.blob() : null)
        .then(blob => blob && createImageBitmap(blob, {premultiplyAlpha: 'none', colorSpaceConversion: 'none'}))
        .then(bitmap => {
          if (!bitmap) return null;
          const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
          const context = canvas.getContext('2d', {willReadFrequently: true}); context.drawImage(bitmap, 0, 0);
          const rgba = context.getImageData(0, 0, bitmap.width, bitmap.height).data, classes = new Uint8Array(bitmap.width * bitmap.height);
          for (let i = 0; i < classes.length; i++) classes[i] = Math.round(rgba[i * 4] / 20);
          return {classes, size: bitmap.width};
        }).catch(() => null));
    }
    return indexTiles.get(url);
  }
  function classWords(index) {
    if (index === 0) return null;
    if (index <= 2) return {title: source === 'aircraft' ? 'Not mapped' : 'No published value', sub: 'Not the same as quiet'};
    const limit = cutoff(source, metric);
    if (index === 3) return {title: `Under ${limit} dB`, sub: 'Very quiet', colour: coloursFor().below};
    const band = index - 4, low = band === 0 ? null : look.breaks_db[band - 1], high = look.breaks_db[band] ?? null;
    const range = band === 0 ? (limit === 35 ? '35–40 dB' : 'Under 40 dB') : high === null ? '70 dB and over' : `${low}–${high} dB`;
    const reference = (low ?? 35) + (metric === 'Lnight' ? 10 : 0);
    return {title: range, sub: wordFor(reference), colour: coloursFor().bands[band]};
  }
  function clearHover() {
    $('hover').hidden = true;
    if (hoveredHex !== null && map?.getLayer('hex-hover')) map.setFilter('hex-hover', ['==', ['id'], -1]);
    hoveredHex = null;
  }
  function placeHover(e) {
    const tip = $('hover'), area = map.getContainer().getBoundingClientRect();
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.point.x + 16, y = e.point.y + 16;
    if (x + w > area.width - 8) x = e.point.x - w - 16;
    if (y + h > area.height - 8) y = e.point.y - h - 16;
    tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }
  function hoverContent(title, sub, colour, note) {
    const tip = $('hover'), head = el('div', 'hover-head');
    if (colour) { const i = el('i'); i.style.background = colour; head.append(i); }
    head.append(el('strong', '', title));
    tip.replaceChildren(head);
    if (sub) tip.append(el('span', '', sub));
    if (note) tip.append(el('small', '', note));
  }
  async function hover() {
    hoverFrame = 0;
    const e = hoverEvent;
    if (!e || !ready || !manifest) return;
    if (map.isMoving()) { $('hover').hidden = true; return; }
    if (lensOn()) {
      const feature = map.queryRenderedFeatures(e.point, {layers: ['hex-fill']})[0];
      const id = feature ? feature.id : null;
      if (id !== hoveredHex) { hoveredHex = id; map.setFilter('hex-hover', ['==', ['id'], id ?? -1]); }
      if (!feature) { $('hover').hidden = true; return; }
      const p = feature.properties, limit = cutoff(source, metric), who = whoLimit(source, metric);
      hoverContent(`${Math.round(p.quiet * 100)}% under ${limit} dB`, who !== null && p.who !== null ? `${Math.round(p.who * 100)}% at or over the WHO ${who} dB` : '',
                   null, 'Hexagon about 5 km across · click for the exact square');
      placeHover(e); return;
    }
    if (!noiseVisible()) { $('hover').hidden = true; return; }
    const ticket = ++hoverTicket, z = tileZoom(), layer = layerName();
    const scale = 2 ** z * manifest.tiles.tile_size, lat = e.lngLat.lat * Math.PI / 180;
    const px = (e.lngLat.lng + 180) / 360 * scale, py = (1 - Math.log(Math.tan(lat) + 1 / Math.cos(lat)) / Math.PI) / 2 * scale;
    const size = manifest.tiles.tile_size, tx = Math.floor(px / size), ty = Math.floor(py / size);
    const tile = await indexTile(layer, z, tx, ty);
    if (ticket !== hoverTicket || layer !== layerName() || !hoverEvent || map.isMoving()) return;
    const words = tile && classWords(tile.classes[Math.floor(py - ty * size) * tile.size + Math.floor(px - tx * size)]);
    if (!words && outsideEngland(e.point)) {
      hoverContent('Outside England', 'Not mapped yet', null, 'Defra’s noise maps cover England only');
      placeHover(e); return;
    }
    if (!words) { $('hover').hidden = true; return; }
    const zoomedOut = !!manifest.overview && z <= manifest.overview.tiles.zooms[1];
    hoverContent(words.title, words.sub, words.colour, zoomedOut ? 'Area average · click for the exact square' : 'Click for the exact value');
    placeHover(e);
  }
  function outsideEngland(at) {
    return map.getZoom() < WASH_UNTIL && !!map.getLayer('outside-wash') && map.queryRenderedFeatures(at, {layers: ['outside-wash']}).length > 0;
  }
  function setupHover() {
    map.on('mousemove', e => {
      if (!finePointer.matches) return;
      hoverEvent = e;
      if (!hoverFrame) hoverFrame = requestAnimationFrame(hover);
    });
    const stop = () => { hoverEvent = null; ++hoverTicket; clearHover(); };
    map.getCanvas().addEventListener('mouseleave', stop);
    map.on('movestart', stop);
  }

  // ---------- saved places and recorded samples on the map ----------
  function drawSavedMarkers(list) {
    savedMarkers.forEach(m => m.remove()); savedMarkers = [];
    $('places-badge').hidden = !list.length; $('places-badge').textContent = String(list.length);
    if (!map) return;
    list.forEach((p, i) => {
      const button = el('button', 'saved-marker', String.fromCharCode(65 + i)); button.type = 'button';
      button.setAttribute('aria-label', `Saved place ${String.fromCharCode(65 + i)}: ${p.label}`);
      button.addEventListener('click', event => { event.stopPropagation(); inspect([p.longitude, p.latitude], p.label); });
      savedMarkers.push(new maplibregl.Marker({element: button}).setLngLat([p.longitude, p.latitude]).addTo(map));
    });
  }
  function flyTo(coords, zoom, {exact = false} = {}) {
    // Keep the spot clear of the card: to the right of it on a wide screen, above it on a phone.
    const options = {center: coords, zoom: exact ? zoom : Math.max(map.getZoom(), zoom), offset: phone.matches ? [0, -90] : [170, 30]};
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
  function drawRecordedResult() {
    const p = recordedSite.properties;
    showResult(true);
    $('result-eyebrow').textContent = 'Recorded sound site';
    $('spot-title').textContent = p.name;
    const value = el('span', '', `${p.sample_min_db_a.toFixed(1)}–${p.sample_max_db_a.toFixed(1)}`); value.append(el('small', '', ' dB(A)'));
    $('spot-value').replaceChildren(...value.childNodes);
    $('spot-level').replaceChildren();
    $('spot-layer').textContent = `${p.sample_count} short recordings (${p.survey_years.join(', ')})`;
    $('spot-explain').textContent = `LAeq over ${p.duration_min_seconds.toFixed(0)}–${p.duration_max_seconds.toFixed(0)} s. A range across recordings, not uncertainty, and not comparable with yearly averages. Site position is approximate; calibration records not verified.`;
    $('spot-who').textContent = ''; $('spot-area').hidden = true;
    $('result-actions').hidden = true; $('recorded-actions').hidden = false;
  }
  async function drawRecorded() {
    recordedMarkers.forEach(m => m.remove()); recordedMarkers = [];
    if (!$('recorded-toggle').checked) { if (recordedSite) { recordedSite = null; drawResult(); } saveView(); return; }
    try { await loadRecorded(); } catch { $('recorded-status').textContent = 'Recorded samples couldn’t be loaded.'; $('recorded-toggle').checked = false; return; }
    for (const item of recorded.locations.features) {
      const button = el('button', 'recorded-marker', 'R'); button.type = 'button';
      button.setAttribute('aria-label', `Recorded sound site: ${item.properties.name}`);
      button.addEventListener('click', event => { event.stopPropagation(); recordedSite = item; drawResult(); });
      recordedMarkers.push(new maplibregl.Marker({element: button}).setLngLat(item.geometry.coordinates).addTo(map));
    }
    saveView();
  }

  // ---------- search ----------
  // Enter takes the highlighted suggestion, else the best match in the local place list; only a search
  // it has nothing for goes to OpenStreetMap.
  async function search(event) {
    event.preventDefault();
    if (!$('suggestions').hidden && activeOption >= 0) { $('suggestions').children[activeOption].click(); return; }
    const query = $('place').value.trim(), list = $('search-results');
    list.replaceChildren(); list.hidden = true;
    // Latitude, longitude is read here and never sent to a search service.
    const coordinates = query.match(COORDINATES);
    if (coordinates) {
      ++navigation; openSuggestions(false);
      const lat = Number(coordinates[1]), lon = Number(coordinates[2]);
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) { $('search-status').textContent = 'Type latitude first (−90 to 90), then longitude (−180 to 180).'; return; }
      $('search-status').textContent = '';
      return go([lon, lat], `${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    }
    if (query.length < 2 || query.length > 160) { $('search-status').textContent = 'Type a place name or postcode (2–160 characters), or latitude, longitude.'; return; }
    if (placesAvailable() && query.length <= 100) {
      clearTimeout(suggestTimer);
      const ticket = ++navigation;
      if (suggested.query !== query) { $('search-status').textContent = 'Searching…'; await loadSuggestions(query); }
      if (ticket !== navigation) return;
      if (suggested.query === query && suggested.places.length) { chooseSuggestion(0); return; }
    }
    return searchWider(query);
  }
  async function searchWider(query) {
    openSuggestions(false);
    const ticket = ++navigation, list = $('search-results');
    list.replaceChildren(); list.hidden = true;
    $('search-status').textContent = 'Searching OpenStreetMap…';
    try {
      const data = await getJSON('/api/search?' + new URLSearchParams({q: query}), 20000);
      if (ticket !== navigation) return;
      if (!data.places.length) { $('search-status').textContent = 'No places found. Try a postcode, or type latitude, longitude.'; return; }
      const [best, ...others] = data.places;
      go([best.longitude, best.latitude], shortName(best.name));
      const status = $('search-status'); status.replaceChildren(`Showing ${shortName(best.name)}.`);
      if (others.length) {
        const toggle = el('button', 'text-button', `Not this one? ${others.length} other match${others.length > 1 ? 'es' : ''}`);
        toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
        toggle.addEventListener('click', () => { list.hidden = !list.hidden; toggle.setAttribute('aria-expanded', String(!list.hidden)); });
        status.append(' ', toggle);
        for (const place of others) {
          const li = el('li'), button = el('button');
          button.type = 'button'; button.append(el('strong', '', shortName(place.name)), el('span', '', place.name));
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
  function go(coords, name, zoom = 15) {
    if (phone.matches) $('place').blur();
    if (ready) flyTo(coords, zoom); else pendingFly = true;
    inspect(coords, name);
  }

  // ---------- suggestions as you type (OS Open Names, on this computer) ----------
  const COORDINATES = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/;
  let suggestTimer = 0, suggestTicket = 0, suggested = {query: '', places: []}, activeOption = -1;
  const placesAvailable = () => !!manifest?.places;
  function openSuggestions(open) {
    $('suggestions').hidden = !open;
    $('place').setAttribute('aria-expanded', String(open));
    if (!open) markOption(-1);
  }
  function markOption(index) {
    activeOption = index;
    [...$('suggestions').children].forEach((li, i) => li.setAttribute('aria-selected', String(i === index)));
    const li = $('suggestions').children[index];
    if (li) { $('place').setAttribute('aria-activedescendant', li.id); li.scrollIntoView({block: 'nearest'}); }
    else $('place').removeAttribute('aria-activedescendant');
  }
  // Once the map is zoomed in on an area, closer places come first among equally good matches.
  function nearView() {
    if (!map || map.getZoom() < 8) return {};
    const c = map.getCenter();
    return {near: `${c.lng.toFixed(4)},${c.lat.toFixed(4)}`};
  }
  async function loadSuggestions(query) {
    const ticket = ++suggestTicket;
    let found;
    try { found = (await getJSON('/api/places?' + new URLSearchParams({q: query, ...nearView()}), 6000)).places; } catch { found = []; }
    if (ticket !== suggestTicket) return false;
    suggested = {query, places: found};
    return true;
  }
  function onPlaceInput() {
    clearTimeout(suggestTimer);
    const query = $('place').value.trim();
    $('search-status').textContent = '';  // A note about the last place chosen no longer applies.
    if (!placesAvailable() || query.length < 2 || query.length > 100 || COORDINATES.test(query)) {
      ++suggestTicket; suggested = {query: '', places: []}; drawSuggestions(); return;
    }
    suggestTimer = setTimeout(async () => {
      if (await loadSuggestions(query) && $('place').value.trim() === query) drawSuggestions();
    }, 110);
  }
  function option(id, title, detail, onChoose, className = 'suggestion') {
    const li = el('li', className); li.id = id;
    li.setAttribute('role', 'option'); li.setAttribute('aria-selected', 'false');
    li.append(el('strong', '', title));
    if (detail) li.append(el('span', '', detail));
    li.addEventListener('pointerdown', event => event.preventDefault());  // Typing focus stays in the box.
    li.addEventListener('click', onChoose);
    return li;
  }
  function drawSuggestions() {
    const list = $('suggestions'); list.replaceChildren();
    const {query, places: found} = suggested;
    found.forEach((p, i) => list.append(option('suggestion-' + i, p.name, [p.word, p.detail].filter(Boolean).join(' · '), () => chooseSuggestion(i))));
    if (query) {
      list.append(option('suggestion-wider', found.length ? `Search OpenStreetMap for “${query}”` : `Not in the place list. Search OpenStreetMap for “${query}”`,
                         'For businesses and other names. Sends this search to Nominatim.', () => searchWider(query), 'suggestion wider'));
    }
    markOption(-1);
    openSuggestions(list.children.length > 0 && document.activeElement === $('place'));
  }
  function suggestionKeys(event) {
    const count = $('suggestions').children.length;
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && count) {
      event.preventDefault();
      if ($('suggestions').hidden) openSuggestions(true);
      let next = activeOption + (event.key === 'ArrowDown' ? 1 : -1);
      if (next < -1) next = count - 1;
      if (next >= count) next = -1;
      markOption(next);
    } else if (event.key === 'Escape' && !$('suggestions').hidden) {
      event.preventDefault(); event.stopPropagation(); openSuggestions(false);
    }
  }
  // A short name for the spot card: "High Street, Oxford", "OX1 3BD", "Oxford".
  function suggestionLabel(p) {
    const where = p.detail.split(',')[0].trim();
    return (p.kind === 'road' && where ? `${p.name}, ${where}` : p.name).slice(0, 60);
  }
  function chooseSuggestion(index) {
    const p = suggested.places[index];
    if (!p) return;
    ++navigation; openSuggestions(false);
    $('search-results').replaceChildren(); $('search-results').hidden = true;
    $('place').value = suggestionLabel(p);
    const status = $('search-status');
    if (p.area || p.kind === 'postcode_district') {
      // A town, village or postcode district is somewhere to look around, not one spot to check.
      clearSpot();
      if (phone.matches) $('place').blur();
      if (ready) flyTo([p.longitude, p.latitude], p.zoom, {exact: true});
      status.textContent = `Showing ${p.kind === 'postcode_district' ? 'the middle of ' : ''}${p.name}. Click any spot to check it, or choose Area to summarise part of it.`;
      return;
    }
    status.textContent = p.kind === 'road'
      ? `Showing ${suggestionLabel(p)}. This point is on the road itself, which is usually louder than the homes beside it: click your exact spot.`
      : p.kind === 'postcode' ? `Showing ${p.name}. A postcode’s point is at one address in it: click your exact spot.` : `Showing ${p.name}.`;
    go([p.longitude, p.latitude], suggestionLabel(p), p.zoom);
  }

  // ---------- area summaries: drag a box, and every 10 m square in it is counted ----------
  const areaLayer = () => source === 'road' || source === 'rail';
  function boxSize(b) {
    const w = (b[2] - b[0]) * 111.32 * Math.cos((b[1] + b[3]) / 360 * Math.PI), h = (b[3] - b[1]) * 110.54;
    return {w, h, km2: w * h};
  }
  const tooBig = size => size.km2 > manifest.area.max_km2 || Math.max(size.w, size.h) > manifest.area.max_side_km;
  const kmText = km => km < 1 ? `${Math.round(km * 1000)} m` : `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
  const metresText = m => m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`;
  const formatKm2 = km2 => `${km2 < 1 ? km2.toFixed(2) : km2 < 10 ? km2.toFixed(1) : Math.round(km2)} km²`;
  const pct = share => share === null ? '—' : share > 0 && share < .005 ? 'under 1%' : `${Math.round(share * 100)}%`;
  function boxFeature(b, warn = false) {
    return {type: 'Feature', properties: {warn}, geometry: {type: 'Polygon',
            coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]]}};
  }
  function areaHint(text, warn = false) {
    $('area-hint').hidden = false; $('area-hint-text').textContent = text; $('area-hint').classList.toggle('warn', warn);
  }
  const drawHandlers = () => [map.dragPan, map.boxZoom, map.doubleClickZoom, map.touchZoomRotate];
  function startDrawing() {
    if (!ready || drawing) return;
    closeDrawerOnPhone(); hideTip(); clearHover();
    drawing = {start: null, box: null};
    document.body.classList.add('drawing-area'); $('open-area').setAttribute('aria-pressed', 'true');
    for (const handler of drawHandlers()) handler.disable();
    areaHint(`Drag a box over the map, up to ${manifest.area.max_km2} km².`);
  }
  function stopDrawing() {
    if (!drawing) return;
    drawing = null;
    document.body.classList.remove('drawing-area'); $('open-area').setAttribute('aria-pressed', String(!!area));
    for (const handler of drawHandlers()) handler.enable();
    map.touchZoomRotate.disableRotation();
    $('area-hint').hidden = true;
    drawAreaOnMap();
  }
  function pointerLngLat(event) {
    const r = map.getCanvasContainer().getBoundingClientRect(), p = map.unproject([event.clientX - r.left, event.clientY - r.top]);
    return [p.lng, p.lat];
  }
  function setupDrawing() {
    const surface = map.getCanvasContainer();
    const reset = () => map.getSource('area-box')?.setData(area ? boxFeature(area.box) : empty);
    surface.addEventListener('pointerdown', event => {
      if (!drawing || !event.isPrimary || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      surface.setPointerCapture(event.pointerId);
      Object.assign(drawing, {start: pointerLngLat(event), pixel: [event.clientX, event.clientY], pointer: event.pointerId, box: null});
    });
    surface.addEventListener('pointermove', event => {
      if (!drawing?.start || event.pointerId !== drawing.pointer) return;
      const a = drawing.start, b = pointerLngLat(event);
      drawing.box = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
      const size = boxSize(drawing.box), big = tooBig(size);
      map.getSource('area-box')?.setData(boxFeature(drawing.box, big));
      areaHint(`${kmText(size.w)} × ${kmText(size.h)}, ${formatKm2(size.km2)}`
               + (big ? `. Too big: up to ${manifest.area.max_km2} km² and ${manifest.area.max_side_km} km across.` : ''), big);
    });
    surface.addEventListener('pointerup', event => {
      if (!drawing?.start || event.pointerId !== drawing.pointer) return;
      quietClicksUntil = Date.now() + 500;  // The click that ends a box doesn't also check a spot.
      const box = drawing.box, moved = Math.hypot(event.clientX - drawing.pixel[0], event.clientY - drawing.pixel[1]);
      drawing.start = null;
      if (!box || moved < 8) { reset(); areaHint(`Drag, rather than click, to draw a box. Up to ${manifest.area.max_km2} km².`); return; }
      if (tooBig(boxSize(box))) return;  // The hint already says why; the next drag starts afresh.
      stopDrawing(); setArea(box);
    });
    surface.addEventListener('pointercancel', event => {
      if (drawing?.start && event.pointerId === drawing.pointer) { drawing.start = null; reset(); }
    });
  }
  // The part of the map you can see: on a wide screen the open drawer covers its right side.
  function useView() {
    const r = map.getContainer().getBoundingClientRect();
    const hidden = !phone.matches && !$('drawer').hidden ? $('drawer').offsetWidth : 0;
    const sw = map.unproject([0, r.height]), ne = map.unproject([Math.max(r.width - hidden, 1), 0]);
    const box = [sw.lng, sw.lat, ne.lng, ne.lat], size = boxSize(box);
    if (tooBig(size)) {
      areaHint(`This view is about ${formatKm2(size.km2)}. Zoom in until it is under ${manifest.area.max_km2} km², or drag a smaller box.`, true);
      return;
    }
    stopDrawing(); setArea(box);
  }
  function setArea(box, level = null, {open = true} = {}) {
    area = {box, level, result: null, error: null, loading: false};
    $('open-area').setAttribute('aria-pressed', 'true');
    if (open) { openDrawer('area'); keepAreaInView(); }
    summarise();
  }
  function clearArea() {
    ++areaTicket; area = null;
    $('open-area').setAttribute('aria-pressed', 'false');
    drawAreaOnMap(); drawAreaPanel(); saveView();
  }
  // On a wide screen the drawer covers the right of the map: keep the box clear of it.
  function keepAreaInView() {
    if (!area || phone.matches || !ready) return;
    const left = Math.min(document.querySelector('.dock-left').getBoundingClientRect().right + 16, window.innerWidth * .4);
    const right = ($('drawer').hidden ? 0 : $('drawer').offsetWidth) + 24;
    const nw = map.project([area.box[0], area.box[3]]), se = map.project([area.box[2], area.box[1]]);
    if (nw.x >= left && se.x <= window.innerWidth - right && nw.y >= 16 && se.y <= window.innerHeight - 16) return;
    map.fitBounds([[area.box[0], area.box[1]], [area.box[2], area.box[3]]],
                  {padding: {left, right, top: 40, bottom: 40}, maxZoom: map.getZoom(), duration: reduceMotion.matches ? 0 : 600});
  }
  const areaQuery = () => new URLSearchParams({layer: layerName(), bbox: area.box.map(v => v.toFixed(6)).join(','), release: manifest.release_id,
                                               ...(area.level === null ? {} : {level: String(area.level)})});
  async function summarise() {
    if (!area) return;
    const ticket = ++areaTicket;
    Object.assign(area, {result: null, error: null, loading: areaLayer()});
    drawAreaOnMap(); drawAreaPanel(); saveView();
    if (!areaLayer()) return;
    try {
      const response = await fetch('/api/england/area?' + areaQuery(), {signal: AbortSignal.timeout(60000)});
      const data = await response.json().catch(() => ({}));
      if (ticket !== areaTicket) return;
      if (response.status === 409) throw new Error('The map data has been updated. Reload the page to summarise this area.');
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'This area couldn’t be summarised. Try again.');
      area.result = data;
    } catch (error) {
      if (ticket !== areaTicket) return;
      area.error = error.name === 'TimeoutError' ? 'This took too long. Try a smaller area.'
        : error instanceof TypeError ? 'This area couldn’t be summarised. Is Quiet UK still running?' : error.message;
    }
    area.loading = false;
    drawAreaOnMap(); drawAreaPanel();
  }
  function drawAreaOnMap() {
    areaMarkers.forEach(m => m.remove()); areaMarkers = [];
    if (!ready || !map.getSource('area-box') || drawing?.start) return;
    map.getSource('area-box').setData(area ? boxFeature(area.box) : empty);
    const r = area?.result, features = [];
    if (r && r.layer === layerName()) {
      for (const [key, text] of [['quietest', 'Quietest'], ['loudest', 'Loudest']]) {
        const s = r[key];
        if (!s) continue;
        features.push({type: 'Feature', properties: {kind: key}, geometry: s.cell});
        const button = el('button', `area-marker ${key}`, text); button.type = 'button';
        button.setAttribute('aria-label', `${text} spot in the selected area: check it`);
        button.addEventListener('click', event => { event.stopPropagation(); showAreaSpot(key); });
        areaMarkers.push(new maplibregl.Marker({element: button, anchor: 'bottom', offset: [0, -6]}).setLngLat([s.longitude, s.latitude]).addTo(map));
      }
      const q = r.quietest;
      if (q?.nearest) {
        features.push({type: 'Feature', properties: {kind: 'link'},
                       geometry: {type: 'LineString', coordinates: [[q.longitude, q.latitude], [q.nearest.longitude, q.nearest.latitude]]}});
      }
    }
    map.getSource('area-marks').setData({type: 'FeatureCollection', features});
  }
  function areaFigure(list, term, value, note) {
    const box = el('div'), dd = el('dd', '', value);
    if (note) dd.append(el('small', '', note));
    box.append(el('dt', '', term), dd); list.append(box);
  }
  function quietNote(q, r) {
    const d = metresText(q.clearance_m), level = `${r.level_db} dB`;
    const reach = q.limited_by === 'search_edge' ? `At least ${d} from any square at or over ${level}.`
      : q.limited_by === 'at_or_over_level' ? `${d} from the nearest square at or over ${level}.`
      : `${d} from the nearest square with no published value (the sea, or outside England), which could hide noise.`;
    const first = q.kind === 'lowest_value' ? `No square here is under ${r.cutoff_db} dB, so this is the lowest published level. ` : '';
    return first + reach + (q.tied_cells > 1 ? ` ${q.tied_cells.toLocaleString()} squares are just as far; this is one of them.` : '');
  }
  function drawAreaPanel() {
    $('area-empty').hidden = !!area; $('area').hidden = !area;
    if (!area) return;
    const r = area.result, limit = cutoff(source, metric), calculated = r ? r.cells.value + r.cells.below_cutoff : 0;
    $('area-layer').textContent = `${SOURCES[source]} noise · ${TIME[metric]}`;
    $('area-title').textContent = `${formatKm2(r ? r.area_km2 : boxSize(area.box).km2)} selected`;
    $('area-status').textContent = !areaLayer() ? 'Area summaries are for road and rail noise. Aircraft noise is only mapped around major airports, so choose Road or Rail.'
      : area.loading ? 'Counting every 10 m square…' : area.error ? area.error
      : r && !calculated ? 'Defra publishes no values in this area (the sea, or outside England). That isn’t the same as quiet.' : '';
    const shown = !!r && calculated > 0 && r.layer === layerName();
    $('area-summary').hidden = !shown;
    if (!shown) return;
    // How the calculated squares divide between the map's colour bands.
    const p = coloursFor(), rows = [];
    if (r.below_cutoff.cells) rows.push({colour: p.below, range: `Under ${limit} dB`, word: 'Very quiet', share: r.below_cutoff.share});
    r.bands.forEach((b, k) => {
      if (!b.cells) return;
      const range = k === 0 ? (limit === 35 ? '35–40 dB' : 'Under 40 dB') : b.high_db === null ? '70 dB and over' : `${b.low_db}–${b.high_db} dB`;
      rows.push({colour: p.bands[k], range, word: wordFor((b.low_db ?? 35) + (metric === 'Lnight' ? 10 : 0)), share: b.share});
    });
    const bar = $('area-bar'); bar.replaceChildren();
    for (const row of rows) {
      const i = el('i'); i.style.background = row.colour; i.style.flexGrow = String(row.share); i.title = `${row.range}: ${pct(row.share)}`; bar.append(i);
    }
    bar.setAttribute('aria-label', rows.map(row => `${row.range}: ${pct(row.share)}`).join(', '));
    const body = $('area-bands').tBodies[0]; body.replaceChildren();
    for (const row of rows) {
      const tr = el('tr'), swatch = el('td'), i = el('i');
      i.style.background = row.colour; swatch.append(i);
      tr.append(swatch, el('td', '', row.range), el('td', 'word', row.word), el('td', 'share', pct(row.share))); body.append(tr);
    }
    $('area-bands-note').textContent = r.cells.no_data
      ? `Shares are of the ${formatKm2(calculated / 1e4)} Defra calculated. ${formatKm2(r.cells.no_data / 1e4)} has no published value and isn’t counted.` : '';
    const figures = $('area-figures'); figures.replaceChildren();
    const m = r.median_db, a = r.energy_average_db;
    areaFigure(figures, 'Typical square', m.value_db === null ? `Under ${m.under_db} dB` : `${m.value_db.toFixed(1)} dB`, 'Half the squares are at or under this');
    areaFigure(figures, 'Very quiet', pct(r.below_cutoff.share), `Under ${limit} dB`);
    if (r.who_2018) areaFigure(figures, 'Over the WHO guideline', pct(r.who_2018.share), `At or over ${r.who_2018.limit_db} dB`);
    else areaFigure(figures, 'WHO guideline', 'None', 'The WHO gives no daytime-only value');
    areaFigure(figures, 'Energy average', a.lower_db === null ? `Under ${limit} dB`
      : a.upper_db - a.lower_db < .05 ? `${a.upper_db.toFixed(1)} dB` : `${a.lower_db.toFixed(1)}–${a.upper_db.toFixed(1)} dB`,
      'Averaged as sound energy, so the loudest squares weigh most');
    const q = r.quietest, l = r.loudest;
    $('area-quiet').hidden = !q;
    if (q) {
      $('area-quiet-main').textContent = q.kind === 'below_cutoff' ? `Under ${limit} dB` : `${q.value_db.toFixed(1)} dB`;
      $('area-quiet-note').textContent = quietNote(q, r);
      const who = whoLimit(source, metric), levels = new Set([r.level_db]);
      for (let v = limit; v <= 70; v += 5) levels.add(v);
      if (who !== null) levels.add(who);
      $('area-level').replaceChildren(...[...levels].sort((x, y) => x - y).map(v => {
        const o = el('option', '', `${v} dB${v === who ? ' (WHO guideline)' : ''}`); o.value = String(v); o.selected = v === r.level_db; return o;
      }));
    }
    $('area-loud').hidden = !l;
    if (l) {
      $('area-loud-main').textContent = `${l.value_db.toFixed(1)} dB`;
      $('area-loud-note').textContent = `${wordFor(metric === 'Lnight' ? l.value_db + 10 : l.value_db)}. Usually on the ${source === 'rail' ? 'railway' : 'road'} itself.`
        + (l.tied_cells > 1 ? ` ${l.tied_cells} squares share this level.` : '');
    }
    $('area-caveat').textContent = `Defra’s modelled yearly averages for ${plainSource(source)} only, counted over every 10 m square whose centre is in the box. Other sounds, and other kinds of transport, aren’t included.`;
    $('area-json').href = '/downloads/england-area.json?' + areaQuery();
    $('area-csv').href = '/downloads/england-area.csv?' + areaQuery();
  }
  function showAreaSpot(key) {
    const s = area?.result?.[key];
    if (!s) return;
    closeDrawerOnPhone(); ++navigation;
    if (ready) flyTo([s.longitude, s.latitude], 16);
    inspect([s.longitude, s.latitude]);
  }

  // ---------- the drawer ----------
  const TABS = ['spot', 'area', 'places', 'about'];
  function selectTab(tab, focus = false) {
    for (const name of TABS) {
      const button = $('tab-' + name), on = name === tab;
      button.setAttribute('aria-selected', String(on)); button.tabIndex = on ? 0 : -1;
      $('panel-' + name).hidden = !on;
    }
    if (focus) $('tab-' + tab).focus();
  }
  function openDrawer(tab) {
    if ($('drawer').hidden) lastFocus = document.activeElement;
    $('drawer').hidden = false; document.body.classList.add('drawer-open');
    selectTab(tab, true);
    $('drawer').querySelector('.drawer-body').scrollTop = 0;
  }
  function closeDrawer() {
    if ($('drawer').hidden) return;
    $('drawer').hidden = true; document.body.classList.remove('drawer-open');
    if (lastFocus?.isConnected) lastFocus.focus({preventScroll: true});
  }
  function setupDrawer() {
    for (const name of TABS) $('tab-' + name).addEventListener('click', () => selectTab(name));
    $('drawer').querySelector('.tabs').addEventListener('keydown', event => {
      const current = TABS.findIndex(name => $('tab-' + name).getAttribute('aria-selected') === 'true');
      const step = {ArrowRight: 1, ArrowLeft: -1, Home: -current, End: TABS.length - 1 - current}[event.key];
      if (step === undefined) return;
      event.preventDefault(); selectTab(TABS[(current + step + TABS.length) % TABS.length], true);
    });
    $('close-drawer').addEventListener('click', closeDrawer);
    $('open-places').addEventListener('click', () => openDrawer('places'));
    $('open-about').addEventListener('click', () => openDrawer('about'));
    $('open-details').addEventListener('click', openDetails);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return;
      if (drawing) stopDrawing(); else closeDrawer();
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
  function padding() {
    if (phone.matches) {
      const top = document.querySelector('.dock-right').getBoundingClientRect().bottom + 8;
      return {top, bottom: window.innerHeight - $('legend').getBoundingClientRect().top + 8, left: 12, right: 12};
    }
    const left = document.querySelector('.dock-left').getBoundingClientRect().right + 16;
    return {top: 24, bottom: 24, left: Math.min(left, window.innerWidth * .4), right: 24};
  }
  function onStyle() {
    addLayers();
    ready = true;
    showOnMap();
    drawSpot(); syncCells(); syncHexes(); drawAreaOnMap();
    map.once('idle', () => loading(null));
  }
  function setupMap() {
    map.on('moveend', () => { syncCells(); saveView(); drawArea(); });
    map.on('zoom', drawScaleNote);
    map.on('sourcedata', event => {
      if (cellLayers.includes(event.sourceId)) refreshCells();
      if (event.sourceId === 'noise' && map.isSourceLoaded('noise')) loading(null);
    });
    map.on('idle', () => { loading(null); if (ready) refreshCells(); });
    map.once('idle', showTip);
    map.on('click', event => {
      if (drawing || Date.now() < quietClicksUntil) return;
      ++navigation; inspect([event.lngLat.lng, event.lngLat.lat]);
    });
    map.getCanvas().addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); inspectCentre(); }
    });
    setupHover(); setupDrawing();
  }
  function inspectCentre() { const c = map.getCenter(); ++navigation; inspect([c.lng, c.lat]); }
  async function share(statusId) {
    saveView();
    try { await navigator.clipboard.writeText(window.location.href); $(statusId).textContent = 'Link copied. It opens this view on this computer while Quiet UK is running.'; }
    catch { $(statusId).textContent = 'Copy the address from your browser’s address bar to share this view.'; }
  }

  // The cards' heights feed the layout (how tall the spot card may grow, where phone controls sit).
  function trackSizes() {
    const root = document.documentElement.style;
    new ResizeObserver(() => root.setProperty('--legend-h', `${$('legend').offsetHeight}px`)).observe($('legend'));
    new ResizeObserver(() => root.setProperty('--search-h', `${document.querySelector('.searchbar').offsetHeight}px`)).observe(document.querySelector('.searchbar'));
    new ResizeObserver(() => { root.setProperty('--result-h', `${$('result').offsetHeight}px`); keepSpotInView(); }).observe($('result'));
  }
  // On a phone the card covers the lower part of the map: nudge the map so the chosen spot stays above it.
  function keepSpotInView() {
    if (!phone.matches || !ready || !point || $('result').hidden || map.isMoving()) return;
    const top = $('result').getBoundingClientRect().top, y = map.project(point).y;
    if (y > top - 32) map.panBy([0, y - (top - 32) / 2], {duration: reduceMotion.matches ? 0 : 400});
  }

  async function start() {
    setupDrawer(); trackSizes();
    document.querySelector(`input[name="look"][value="${lookName}"]`).checked = true;
    $('theme-color').content = LOOKS[lookName].background;
    const firstStyle = styleFor(lookName);
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
    $('lens').checked = hash.get('lens') === '1';
    document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
    document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
    if (hash.has('look')) storage.set('quietuk-look', lookName);
    drawLegend(); drawHelpScale(); drawResearch();
    places = window.createEnglandPlaces({
      manifest, describe,
      onShow: p => { closeDrawerOnPhone(); if (ready) flyTo([p.longitude, p.latitude], 15); inspect([p.longitude, p.latitude], p.label); },
      onChange: list => drawSavedMarkers(list),
      initialHash: hash,
    });
    if (hash.has('v') && hash.get('v') !== manifest.release_id) message('This link was made with an older version of the map data, so the current data is shown.');
    if (manifest.overview_problem) message('The zoomed-out overview failed its checks, so zoomed-out colours are samples. The exact data is unaffected.');

    // Controls work straight away; a spot chosen before the map is ready is checked once it is.
    $('sources').addEventListener('change', event => choose(event.target.value, metric));
    $('metrics').addEventListener('change', event => choose(source, event.target.value));
    $('looks').addEventListener('change', event => setLook(event.target.value));
    $('overlay').addEventListener('change', () => { if (ready) { refreshCells(); syncCells(); } drawLegend(); clearHover(); saveView(); });
    $('lens').addEventListener('change', () => { drawLegend(); if (ready) syncHexes(); clearHover(); saveView(); drawResult(); });
    $('legend-change').addEventListener('click', () => {
      const open = !$('legend').classList.contains('expanded');
      $('legend').classList.toggle('expanded', open); $('legend-change').setAttribute('aria-expanded', String(open));
      $('legend-change').textContent = open ? 'Done' : 'Change';
    });
    $('search').addEventListener('submit', search);
    $('place').addEventListener('input', onPlaceInput);
    $('place').addEventListener('keydown', suggestionKeys);
    $('place').addEventListener('focus', () => { if (suggested.query && suggested.query === $('place').value.trim()) drawSuggestions(); });
    $('place').addEventListener('blur', () => openSuggestions(false));
    $('open-area').addEventListener('click', () => { if (drawing) stopDrawing(); else startDrawing(); });
    $('area-use-view').addEventListener('click', useView);
    $('area-cancel').addEventListener('click', stopDrawing);
    $('clear-area').addEventListener('click', clearArea);
    $('area-again').addEventListener('click', startDrawing);
    $('area-level').addEventListener('change', () => { if (area) { area.level = Number($('area-level').value); summarise(); } });
    $('area-quiet-show').addEventListener('click', () => showAreaSpot('quietest'));
    $('area-loud-show').addEventListener('click', () => showAreaSpot('loudest'));
    $('inspect-centre').addEventListener('click', () => ready && inspectCentre());
    $('inspect-centre-panel').addEventListener('click', () => { if (!ready) return; closeDrawerOnPhone(); inspectCentre(); });
    $('share-view').addEventListener('click', () => share('share-status'));
    $('share-view-panel').addEventListener('click', () => share('share-status-panel'));
    $('close-result').addEventListener('click', clearSpot);
    $('check-recorded').addEventListener('click', () => {
      const item = recordedSite; if (!item) return;
      if (ready) flyTo(item.geometry.coordinates, 15);
      inspect(item.geometry.coordinates, item.properties.name);
    });
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
      container: 'map', style: await firstStyle, minZoom: 5, maxZoom: 19, dragRotate: false, touchPitch: false, pitchWithRotate: false,
      attributionControl: {compact: true},
      ...(saved ? {center: camera.slice(0, 2), zoom: camera[2]} : {bounds: ENGLAND, fitBoundsOptions: {padding: padding()}}),
    });
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-right');
    map.getCanvas().setAttribute('aria-label', 'Noise map. Arrow keys move, plus and minus zoom, Enter checks the spot at the centre.');
    // The basemaps name a few icons their sprite lacks; a blank stands in so they fail quietly.
    map.on('styleimagemissing', event => { if (!map.hasImage(event.id)) map.addImage(event.id, {width: 1, height: 1, data: new Uint8Array(4)}); });
    setupMap();
    map.on('style.load', onStyle);
    if (map.isStyleLoaded()) onStyle();
    map.once('load', () => {
      // Phones open the attribution expanded over the map; start it folded behind its (i) button.
      document.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show');
      drawSavedMarkers(places.places());
      if (pendingFly && point) flyTo(point, 15);
      // A shared or restored view can come from a wider screen: keep the chosen spot in sight.
      else if (point && !map.getBounds().contains(point)) map.jumpTo({center: point});
      pendingFly = false;
      if (hash.get('samples') === '1' && app?.observations_release) { $('recorded-toggle').checked = true; drawRecorded(); }
    });
    setTimeout(() => loading(null), 20000);

    // A shared view can carry a selected area; it is summarised again from the current data.
    const savedArea = hash.get('area')?.split(',').map(Number);
    if (savedArea?.length === 4 && savedArea.every(Number.isFinite) && savedArea[0] < savedArea[2] && savedArea[1] < savedArea[3]) {
      const level = Number(hash.get('level'));
      setArea(savedArea, hash.has('level') && Number.isFinite(level) ? level : null, {open: !phone.matches});
    }
    const savedPoint = hash.get('point')?.split(',').map(Number);
    if (savedPoint?.length === 2 && savedPoint.every(Number.isFinite) && Math.abs(savedPoint[0]) <= 180 && Math.abs(savedPoint[1]) <= 90) {
      const label = hash.get('label');
      inspect(savedPoint, label && label.length <= 60 && !/[\x00-\x1f\x7f]/.test(label) ? label : null);
    }
  }
  function closeDrawerOnPhone() { if (phone.matches) closeDrawer(); }
  // Pasting another view link into this tab changes only the fragment; reload to apply it.
  // Our own replaceState updates do not fire hashchange.
  window.addEventListener('hashchange', () => window.location.reload());
  phone.addEventListener('change', () => map?.resize());
  start();
})();
