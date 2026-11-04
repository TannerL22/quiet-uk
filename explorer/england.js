(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const metrics = ['Lden', 'Lday', 'Lnight'];
  const labels = {Lden: 'Daily average · Lden', Lday: 'Day · 07:00–19:00', Lnight: 'Night · 23:00–07:00'};
  const empty = {type: 'FeatureCollection', features: []};
  // Close up, exact native-cell images replace the zoom-14 tiles, which can
  // shift a cell edge by up to one tile pixel (about 6 m).
  const CELL_ZOOM = 15.5;
  let manifest, map, marker, location, point;
  let source = 'road', metric = 'Lden', selection = 0, navigation = 0;
  let cellLayers = [], cellTicket = 0;
  const message = text => { $('message').textContent = text; $('message').hidden = !text; };
  const tileUrl = () => `/england-tiles/${manifest.release_id}/${source}-${metric}/{z}/{x}/{y}.png`;
  const cutoff = (s, m) => manifest.evidence_contract.zero_rule_sources.includes(s) ? (m === 'Lnight' ? 35 : 40) : null;
  const formatted = o => o.value_db !== null ? `${o.value_db.toFixed(1)} dB(A)`
    : o.status === 'outside_extract' ? 'Not mapped here' : o.display_label;
  const short = o => o.value_db !== null ? o.value_db.toFixed(1)
    : o.status === 'verified_below_cutoff' ? `<${o.bounds.upper_db}` : o.status === 'outside_extract' ? '—' : 'Unknown';

  function saveView() {
    const params = new URLSearchParams({source, metric, v: manifest.release_id, noise: $('overlay').checked ? '1' : '0'});
    if (map) { const c = map.getCenter(); params.set('camera', [c.lng.toFixed(6), c.lat.toFixed(6), map.getZoom().toFixed(2)].join(',')); }
    if (point) params.set('point', point.map(v => v.toFixed(6)).join(','));
    history.replaceState(null, '', '#'+params.toString());
  }
  function renderPoint() {
    $('comparison').replaceChildren();
    if (!location) return;
    const active = location.observations.find(o => o.source === source && o.metric === metric);
    $('point-value').textContent = formatted(active);
    $('map-value').textContent = `${$('point-value').textContent} · ${names[source]} ${metric} at the selected cell`;
    $('point-status').textContent = active.value_db !== null
      ? `${names[source]} · ${labels[metric]} · modelled in a 10 m cell`
      : active.status === 'verified_below_cutoff' ? `Defra's model calculated a level under ${active.bounds.upper_db} dB here. The exact value is not published.`
      : active.status === 'outside_extract' ? 'This layer has no published grid here. Not mapped does not mean quiet.'
      : 'Defra publishes no value for this cell. No data does not mean quiet.';
    for (const key of Object.keys(names)) {
      const tr = document.createElement('tr'), th = document.createElement('th');
      th.scope = 'row'; th.textContent = names[key]; tr.append(th);
      for (const m of metrics) {
        const o = location.observations.find(x => x.source === key && x.metric === m), td = document.createElement('td');
        td.textContent = short(o);
        td.title = o.value_db !== null ? 'Modelled value' : o.status === 'outside_extract' ? 'Not mapped here' : o.explanation;
        if (key === source && m === metric) td.className = 'active';
        tr.append(td);
      }
      $('comparison').append(tr);
    }
    map.getSource('selected-cell').setData(active.cell ? {type: 'Feature', properties: {}, geometry: active.cell} : empty);
    $('point-download').hidden = false;
    $('point-download').href = '/downloads/england-location.json?'+new URLSearchParams({lon: location.longitude, lat: location.latitude, release: manifest.release_id});
  }
  async function inspect(coords) {
    const current = ++selection;
    point = coords; location = null;
    $('point-title').textContent = `${coords[1].toFixed(5)}, ${coords[0].toFixed(5)}`;
    $('point-value').textContent = 'Checking this point…'; $('map-value').textContent = 'Checking selected point…';
    $('point-status').textContent = ''; $('comparison').replaceChildren(); $('point-download').hidden = true;
    map.getSource('selected-cell').setData(empty);
    if (!marker) marker = new maplibregl.Marker({color: '#294339'});
    marker.setLngLat(coords).addTo(map);
    saveView();
    try {
      const response = await fetch('/api/england/location?'+new URLSearchParams({lon: coords[0], lat: coords[1], release: manifest.release_id}), {signal: AbortSignal.timeout(15000)});
      if (response.status === 409) throw new Error('release_mismatch');
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (current !== selection) return;
      if (data.release_id !== manifest.release_id) throw new Error('release_mismatch');
      location = data; renderPoint();
    } catch (error) {
      if (current !== selection) return;
      $('point-value').textContent = 'Evidence unavailable'; $('map-value').textContent = 'Point evidence unavailable';
      $('point-status').textContent = error.message === 'release_mismatch'
        ? 'The server is using a different release. Reload this page to inspect the current evidence.'
        : 'The data could not be verified or loaded. Select a point to try again.';
    }
  }
  const cellMode = () => $('overlay').checked && map.getZoom() >= CELL_ZOOM;
  function refreshCells() {
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
      const b = map.getBounds(), dx = (b.getEast()-b.getWest())*.1, dy = (b.getNorth()-b.getSouth())*.1;
      const bbox = [b.getWest()-dx, b.getSouth()-dy, b.getEast()+dx, b.getNorth()+dy].map(v => v.toFixed(6)).join(',');
      try {
        const response = await fetch('/api/england/cells?'+new URLSearchParams({layer: `${source}-${metric}`, bbox, release: manifest.release_id}), {signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error();
        windows = (await response.json()).windows;
      } catch { windows = []; }
      if (ticket !== cellTicket) return;
    }
    const ids = new Set(windows.map(w => 'cells-'+w.id));
    for (const id of cellLayers) if (!ids.has(id)) { map.removeLayer(id); map.removeSource(id); }
    cellLayers = cellLayers.filter(id => ids.has(id));
    for (const w of windows) {
      const id = 'cells-'+w.id;
      if (cellLayers.includes(id)) continue;
      map.addSource(id, {type: 'image', url: w.url, coordinates: w.corners});
      map.addLayer({id, type: 'raster', source: id,
        paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 0}}, 'cell-outline');
      cellLayers.push(id);
    }
    refreshCells();
  }
  function updateLayer() {
    map.getSource('noise').setTiles([tileUrl()]);
    syncCells();
    const p = manifest.products[source], limit = cutoff(source, metric);
    $('legend-title').textContent = `${names[source]} · ${labels[metric]} · dB(A)`;
    $('map-title').textContent = `${names[source]} · ${labels[metric]}`;
    $('source-scope').textContent = `${names[source]} only · other sources are excluded. This is not overall sound exposure.`;
    $('metric-note').textContent = metric === 'Lden' ? '*Lden: annual daily energy average, with added weighting for evening and night. It does not describe background sound or individual loud events.' : manifest.metrics[metric];
    $('period').textContent = p.reference_period ? `Reference period: ${p.reference_period.start.slice(0, 4)} · 10 m grid · 4 m above ground` : 'Aircraft reference period: unspecified by provider · 10 m grid';
    $('provider').href = 'https://environment.data.gov.uk/dataset/'+p.metadata_id;
    $('unknown-legend').replaceChildren();
    for (const item of manifest.evidence_contract.display_missing) {
      if (item.encoding === 'zero_code' && limit === null) continue;
      const span = document.createElement('span'), swatch = document.createElement('i');
      swatch.style.background = item.encoding === 'tiff_nodata'
        ? `repeating-linear-gradient(45deg, ${item.colour} 0 2px, ${item.colour}66 2px 8px)` : item.colour;
      span.append(swatch, document.createTextNode(item.label.replace('{cutoff}', limit)));
      $('unknown-legend').append(span);
    }
    if (source === 'aircraft') {
      const span = document.createElement('span'); span.textContent = 'Blank: not mapped (aircraft is mapped around airports only).';
      $('unknown-legend').append(span);
    }
    renderPoint(); saveView();
  }
  function overlayStatus(exact) {
    if (!$('overlay').checked) { $('overlay-status').textContent = 'Noise overlay hidden.'; return; }
    $('overlay-status').textContent = exact ? 'Exact 10 m cells, drawn from the original Defra grid.'
      : map.getZoom() < 13.5 ? 'Zoomed out: each pixel shows the 10 m cell at its centre. Zoom in to see every cell.'
      : cellMode() ? 'Loading exact 10 m cells…' : 'Every 10 m cell is drawn. Zoom in further for exact cell edges.';
  }
  async function findPlace(coords, label, ticket, move = true) {
    $('search-results').hidden = true;
    if (ticket !== navigation) return;
    $('search-status').textContent = label;
    if (move) map.jumpTo({center: coords, zoom: Math.max(map.getZoom(), 15)});
    await inspect(coords);
  }
  async function search(event) {
    event.preventDefault();
    const ticket = ++navigation, query = $('place').value.trim();
    $('search-results').replaceChildren(); $('search-results').hidden = true;
    // Latitude, longitude input is resolved locally and never sent to a geocoder.
    const coordinates = query.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (coordinates) {
      const lat = Number(coordinates[1]), lon = Number(coordinates[2]);
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) { $('search-status').textContent = 'Enter latitude from −90 to 90 and longitude from −180 to 180.'; return; }
      return findPlace([lon, lat], query, ticket);
    }
    if (query.length < 2 || query.length > 160) { $('search-status').textContent = 'Enter 2–160 characters, or latitude, longitude.'; return; }
    $('search-status').textContent = 'Searching places…';
    try {
      const response = await fetch('/api/search?'+new URLSearchParams({q: query}), {signal: AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (ticket !== navigation) return;
      $('search-status').textContent = data.places.length ? 'Choose a result.' : 'No places found. Try a postcode or latitude, longitude.';
      for (const place of data.places) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.textContent = place.name;
        button.addEventListener('click', () => findPlace([place.longitude, place.latitude], place.name, ++navigation));
        li.append(button); $('search-results').append(li);
      }
      $('search-results').hidden = !data.places.length;
    } catch {
      if (ticket === navigation) $('search-status').textContent = 'Place search is unavailable. Try latitude, longitude.';
    }
  }
  async function start() {
    try {
      const response = await fetch('/api/england', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error();
      manifest = await response.json();
    } catch {
      message('The England map could not be loaded. Restart Quiet UK with the launcher.'); return;
    }
    const hash = new URLSearchParams(window.location.hash.slice(1));
    if (Object.hasOwn(names, hash.get('source'))) source = hash.get('source');
    if (metrics.includes(hash.get('metric'))) metric = hash.get('metric');
    $('overlay').checked = hash.get('noise') !== '0';
    document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
    document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
    const a = manifest.acquisition;
    $('coverage-title').textContent = a.status === 'sealed' ? `All of England · ${a.planned_tiles.toLocaleString()} squares of 10 km`
      : `Preview · ${a.complete_tiles.toLocaleString()} of ${a.planned_tiles.toLocaleString()} squares`;
    $('coverage-note').textContent = a.status === 'sealed' ? 'Every 10 m cell Defra published for England, checked against the original downloads.'
      : 'The England download is still running. Squares not yet downloaded are blank.';
    const t = manifest.transformation;
    const ostn15 = (t.grids || []).some(g => g.name.includes('OSTN15'));
    $('transformation-note').textContent = `Map positions use ${ostn15 ? "Ordnance Survey's OSTN15 grid" : t.description.replace(/ \(with axis.*\)/, '')} (stated accuracy ${t.stated_accuracy_m} m).`
      + (t.better_operations_not_installed.length ? ' The more accurate OSTN15 grid is not installed.' : '');
    for (const colour of manifest.display.colours) { const span = document.createElement('span'); span.style.background = colour; $('legend-scale').append(span); }
    let style;
    try {
      const base = await fetch('https://tiles.openfreemap.org/styles/positron', {signal: AbortSignal.timeout(10000)});
      if (!base.ok) throw new Error(); style = await base.json();
    } catch {
      style = {version: 8, sources: {}, layers: [{id: 'background', type: 'background', paint: {'background-color': '#e5e9e0'}}]};
      message('The basemap is unavailable. Noise data and point inspection still work.');
    }
    // Open on the saved view so the basemap does not first load tiles for all of England.
    const camera = hash.get('camera')?.split(',').map(Number);
    const view = camera?.length === 3 && camera.every(Number.isFinite) && Math.abs(camera[0]) <= 180 && Math.abs(camera[1]) <= 85
      ? {center: camera.slice(0, 2), zoom: camera[2]} : {center: [-1.5, 52.8], zoom: 6};
    map = new maplibregl.Map({container: 'map', style, ...view, minZoom: 5, maxZoom: 19});
    map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
    map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-right');
    map.on('load', async () => {
      const before = map.getStyle().layers.find(l => l.type === 'symbol')?.id;
      map.addSource('noise', {type: 'raster', tiles: [tileUrl()], tileSize: manifest.tiles.tile_size,
        minzoom: manifest.tiles.zooms[0], maxzoom: manifest.tiles.zooms[1], bounds: [-7, 49.8, 2, 56],
        attribution: 'Noise: Defra, OGL v3'});
      map.addLayer({id: 'noise', type: 'raster', source: 'noise',
        paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 0}}, before);
      map.addSource('selected-cell', {type: 'geojson', data: empty});
      map.addLayer({id: 'cell-outline', type: 'line', source: 'selected-cell', paint: {'line-color': '#203d35', 'line-width': 2}});
      const saved = hash.get('point')?.split(',').map(Number);
      updateLayer();
      if (saved?.length === 2 && saved.every(Number.isFinite) && Math.abs(saved[0]) <= 180 && Math.abs(saved[1]) <= 90) await inspect(saved);
      if (hash.has('v') && hash.get('v') !== manifest.release_id) message('This saved link used a different map release. The current verified release is shown.');
      map.on('moveend', () => { syncCells(); saveView(); });
      map.on('sourcedata', e => { if (cellLayers.includes(e.sourceId)) refreshCells(); });
      map.on('idle', refreshCells);
      map.on('click', e => { ++navigation; inspect([e.lngLat.lng, e.lngLat.lat]); });
      $('sources').disabled = false; $('metrics').disabled = false;
      $('place').disabled = false; $('search-button').disabled = false;
      $('inspect-centre').disabled = false; $('share-view').disabled = false;
      $('search').addEventListener('submit', search);
      $('sources').addEventListener('change', e => { source = e.target.value; updateLayer(); });
      $('metrics').addEventListener('change', e => { metric = e.target.value; updateLayer(); });
      $('overlay').addEventListener('change', updateLayer);
      $('inspect-centre').addEventListener('click', () => { const c = map.getCenter(); ++navigation; inspect([c.lng, c.lat]); });
      $('share-view').addEventListener('click', async () => {
        saveView();
        try { await navigator.clipboard.writeText(window.location.href); $('share-status').textContent = 'View link copied. This local link works on this computer.'; }
        catch { $('share-status').textContent = 'Copy the address bar to share this view on this computer.'; }
      });
    });
  }
  // Pasting another view link into this tab changes only the fragment; reload to apply it.
  // Our own replaceState updates do not fire hashchange.
  window.addEventListener('hashchange', () => window.location.reload());
  start();
})();
