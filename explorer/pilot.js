(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const metrics = ['Lden', 'Lday', 'Lnight'];
  const labels = {Lden: 'Lden', Lday: 'Day · 07:00–19:00', Lnight: 'Night · 23:00–07:00'};
  let manifest, map, marker, location, selection = 0, navigation = 0;
  let site = 'heathrow', source = 'aircraft', metric = 'Lden';
  let point, comparison;
  let savedMarkers = [];
  const empty = {type: 'FeatureCollection', features: []};
  const message = text => { $('message').textContent = text; $('message').hidden = !text; };
  const record = () => manifest.records.find(r => r.site === site && r.source === source && r.metric === metric);
  const feature = geometry => ({type: 'Feature', properties: {}, geometry});
  const areaSize = () => (manifest.sites[site].size_m || 2000)/1000;
  // Pasting another comparison link into this same tab can change only the
  // fragment. Reload to validate its release/recipe just like a fresh visit.
  // Our own replaceState updates do not fire hashchange.
  window.addEventListener('hashchange', () => window.location.reload());
  function saveView() {
    const params = new URLSearchParams({site, source, metric, v: manifest.release_id});
    if (point) params.set('point', point.map(x => x.toFixed(6)).join(','));
    const places = comparison?.getPlaces() || [];
    if (places.length) params.set('compare', JSON.stringify(places));
    history.replaceState(null, '', '#'+params.toString());
  }
  function showSavedMarkers() {
    savedMarkers.forEach(marker => marker.remove()); savedMarkers = [];
    (comparison?.getPlaces() || []).forEach((p,i) => {
      const el = document.createElement('button'); el.className = 'saved-map-marker'; el.textContent = String.fromCharCode(65+i);
      el.setAttribute('aria-label', `Show saved place ${p.label}`);
      el.addEventListener('click', event => {event.stopPropagation(); showSavedPlace(p);});
      savedMarkers.push(new maplibregl.Marker({element:el, anchor:'bottom', offset:[0,-8]}).setLngLat([p.longitude,p.latitude]).addTo(map));
    });
    saveView();
  }
  function showSavedPlace(p) {
    ++navigation; site = p.site; $('site').value = site; location = null;
    updateLayer(); map.jumpTo({center:[p.longitude,p.latitude],zoom:15}); inspect([p.longitude,p.latitude]);
  }
  function renderPoint() {
    comparison?.update(location, source, metric);
    $('comparison').replaceChildren();
    if (!location) return;
    const active = location.observations.find(o => o.source === source && o.metric === metric);
    $('point-title').textContent = `${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`;
    $('point-value').textContent = active.value_db === null ? (active.display_label || (active.status === 'outside_pilot' ? 'Outside coverage' : 'Unreported')) : `${active.value_db.toFixed(1)} dB(A)`;
    $('map-value').textContent = $('point-value').textContent+' · at selected point';
    $('point-status').textContent = active.value_db === null
      ? (active.explanation || (active.status === 'outside_pilot' ? `Select a point inside the outlined ${areaSize()} × ${areaSize()} km area, or use the England map.` : 'Below a reporting cutoff or missing. A quietness value cannot be assigned.'))
      : `${names[source]} · ${labels[metric]} · modelled in a 10 m cell`;
    for (const key of Object.keys(names)) {
      const tr = document.createElement('tr'), th = document.createElement('th');
      th.scope = 'row'; th.textContent = names[key]; tr.append(th);
      for (const m of metrics) {
        const observation = location.observations.find(o => o.source === key && o.metric === m);
        const td = document.createElement('td');
        td.textContent = observation.value_db === null ? '—' : observation.value_db.toFixed(1);
        td.title = observation.explanation || observation.status.replaceAll('_', ' ');
        if (observation.value_db === null && observation.display_label) td.textContent = observation.display_label;
        if (key === source && m === metric) td.className = 'active';
        tr.append(td);
      }
      $('comparison').append(tr);
    }
    const aircraft = location.observations.find(o => o.source === 'aircraft' && o.metric === metric);
    if (source !== 'aircraft' && aircraft.value_db !== null) {
      $('map-value').textContent += ` · aircraft ${aircraft.value_db.toFixed(1)} dB(A) also reported`;
    }
    $('aircraft-cue').hidden = source === 'aircraft';
    $('aircraft-evidence').textContent = aircraft.value_db !== null
      ? `Aircraft is also reported here: ${aircraft.value_db.toFixed(1)} dB(A) ${metric}. It is not included in this ${names[source].toLowerCase()} layer.`
      : 'Aircraft exposure is unknown here. Missing mapped aircraft evidence does not establish an absence of aviation noise.';
    map.getSource('selected-cell').setData(active.cell ? feature(active.cell) : empty);
    $('point-download').hidden = false;
    $('point-download').href = '/downloads/pilot-location.json?'+new URLSearchParams({site, lon: location.longitude, lat: location.latitude});
  }
  async function inspect(coords) {
    const current = ++selection;
    point = coords;
    $('england-map').href = '/#'+new URLSearchParams({map:`${coords[1]},${coords[0]},12`,point:`${coords[1]},${coords[0]}`,view:'both'});
    location = null;
    comparison?.update(null, source, metric);
    $('point-value').textContent = 'Checking this point…';
    $('map-value').textContent = 'Checking selected point…';
    $('point-title').textContent = `${point[1].toFixed(5)}, ${point[0].toFixed(5)}`;
    $('point-status').textContent = '';
    $('comparison').replaceChildren();
    $('aircraft-cue').hidden = true;
    $('point-download').hidden = true;
    map.getSource('selected-cell').setData(empty);
    if (!marker) marker = new maplibregl.Marker({color: '#294339'});
    marker.setLngLat(point).addTo(map);
    saveView();
    try {
      const response = await fetch('/api/pilot/location?'+new URLSearchParams({site, lon: point[0], lat: point[1]}), {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (current !== selection) return;
      location = data;
      renderPoint();
    } catch {
      if (current !== selection) return;
      $('point-value').textContent = 'Evidence unavailable';
      $('map-value').textContent = 'Point evidence unavailable';
      $('point-status').textContent = 'The data could not be verified or loaded. Select a point to try again.';
    }
  }
  function updateLayer() {
    const r = record(), p = manifest.products[source];
    if (map.getLayer('noise')) map.removeLayer('noise');
    if (map.getSource('noise')) map.removeSource('noise');
    map.addSource('noise', {type: 'image', url: `/pilot-images/${manifest.release_id}/${r.id}.png`, coordinates: r.display.corners});
    // Draw underneath geographic labels, but above the basemap's ground layers.
    const before = map.getStyle().layers.find(l => l.type === 'symbol')?.id || 'pilot-outline';
    map.addLayer({id: 'noise', type: 'raster', source: 'noise', layout: {visibility: $('overlay').checked ? 'visible' : 'none'}, paint: {'raster-opacity': .85, 'raster-resampling': 'nearest', 'raster-fade-duration': 0}}, before);
    $('legend-title').textContent = `${names[source]} · ${metric} · dB(A)`;
    $('source-scope').textContent = `${names[source]} only · other sources are excluded. This is not overall sound exposure.`;
    $('unknown-legend').replaceChildren();
    for (const item of manifest.evidence_contract?.display_missing || [{label:'Unreported / unknown', colour:'#c0b8a5'}]) {
      const span = document.createElement('span'), swatch = document.createElement('i');
      swatch.style.backgroundColor = item.colour; span.append(swatch, document.createTextNode(item.label)); $('unknown-legend').append(span);
    }
    $('metric-note').textContent = manifest.metrics[metric];
    $('period').textContent = p.reference_period ? `Reference period: ${p.reference_period.start.slice(0,4)} · 10 m grid · 4 m above ground` : 'Aircraft reference period: unspecified by provider · 10 m grid';
    $('provider').href = 'https://environment.data.gov.uk/dataset/'+p.metadata_id;
    $('map-title').textContent = `${names[source]} · ${labels[metric]}`;
    $('area-size').textContent = `${areaSize()} × ${areaSize()} km`;
    map.getSource('pilot-area').setData(feature(r.footprint));
    renderPoint();
    saveView();
  }
  function recenter() {
    const corners = record().display.corners;
    const bounds = new maplibregl.LngLatBounds();
    corners.forEach(c => bounds.extend(c));
    map.fitBounds(bounds, {padding: {top: 110, bottom: 160, left: 30, right: 30}, duration: 0, maxZoom: 15});
  }
  function chooseSite() {
    recenter();
    inspect(manifest.sites[site].center);
  }
  async function findPoint(coords, label, ticket) {
    $('search-status').textContent = 'Checking detailed coverage…';
    try {
      const response = await fetch('/api/pilot/locate?'+new URLSearchParams({lon:coords[0],lat:coords[1]}), {signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error();
      const found = await response.json();
      if (ticket !== navigation) return;
      $('search-results').hidden = true;
      if (found.sites.length) {
        site = found.sites.includes(site) ? site : found.sites[0];
        $('site').value = site; location = null; updateLayer();
        $('search-status').textContent = `${label} · ${manifest.sites[site].name}`;
      } else {
        $('search-status').textContent = `${label} is outside the four detailed areas. Use the England map for wider coverage.`;
      }
      map.jumpTo({center:coords,zoom:14.5});
      inspect(coords);
    } catch {
      if (ticket === navigation) $('search-status').textContent = 'Coverage could not be checked. Try again or select an area.';
    }
  }
  async function search(event) {
    event.preventDefault();
    const ticket = ++navigation, query = $('place').value.trim();
    $('search-results').replaceChildren(); $('search-results').hidden = true;
    // Latitude, longitude input is resolved locally and never sent to a geocoder.
    const coordinates = query.match(/^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (coordinates) {
      const lat = Number(coordinates[1]), lon = Number(coordinates[2]);
      if (Math.abs(lat)>90 || Math.abs(lon)>180) { $('search-status').textContent = 'Enter latitude from −90 to 90 and longitude from −180 to 180.'; return; }
      return findPoint([lon,lat],query,ticket);
    }
    if (query.length<2 || query.length>160) { $('search-status').textContent = 'Enter 2–160 characters, or latitude, longitude.'; return; }
    $('search-status').textContent = 'Searching places…';
    try {
      const response = await fetch('/api/search?'+new URLSearchParams({q:query}), {signal:AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (ticket !== navigation) return;
      $('search-status').textContent = data.places.length ? 'Choose a result. Detailed coverage will be checked.' : 'No places found. Try a postcode or latitude, longitude.';
      for (const place of data.places) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.textContent = place.name;
        button.addEventListener('click', () => findPoint([place.longitude,place.latitude],place.name,++navigation));
        li.append(button); $('search-results').append(li);
      }
      $('search-results').hidden = !data.places.length;
    } catch {
      if (ticket === navigation) $('search-status').textContent = 'Place search is unavailable. Try latitude, longitude or choose an area.';
    }
  }
  async function start() {
    try {
      const response = await fetch('/api/pilot', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error();
      manifest = await response.json();
      const hash = new URLSearchParams(window.location.hash.slice(1));
      if (manifest.sites[hash.get('site')]) site = hash.get('site');
      if (Object.hasOwn(names, hash.get('source'))) source = hash.get('source');
      if (metrics.includes(hash.get('metric'))) metric = hash.get('metric');
      const savedPoint = hash.get('point')?.split(',').map(Number);
      for (const [id, s] of Object.entries(manifest.sites)) {
        const option = document.createElement('option'); option.value = id; option.textContent = s.name; $('site').append(option);
      }
      $('site').value = site;
      const areaCount = Object.keys(manifest.sites).length;
      const areaTotal = Object.values(manifest.sites).reduce((sum,s)=>sum+((s.size_m||2000)/1000)**2,0);
      $('intro').textContent = `Explore ${areaCount} areas covering ${areaTotal} km² with original 10 m model grids. Choose a source and compare annual day and night averages.`;
      document.querySelector(`input[name="source"][value="${source}"]`).checked = true;
      document.querySelector(`input[name="metric"][value="${metric}"]`).checked = true;
      for (const colour of manifest.display.colours) {const span = document.createElement('span'); span.style.background = colour; $('legend-scale').append(span);}
      let style;
      try {
        const base = await fetch('https://tiles.openfreemap.org/styles/positron', {signal: AbortSignal.timeout(10000)});
        if (!base.ok) throw new Error(); style = await base.json();
      } catch {
        style = {version: 8, sources: {}, layers: [{id: 'background', type: 'background', paint: {'background-color': '#e5e9e0'}}]};
        message('The basemap is unavailable. Source data and point inspection still work.');
      }
      map = new maplibregl.Map({container: 'map', style, center: manifest.sites[site].center, zoom: 14, maxZoom: 20});
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
      map.on('error', e => {if (e.sourceId === 'noise') message('The selected noise overlay could not be loaded. Point inspection still provides verified values.');});
      map.on('load', () => {
        map.addSource('pilot-area', {type: 'geojson', data: empty});
        map.addLayer({id: 'pilot-outline', type: 'line', source: 'pilot-area', paint: {'line-color': '#506d51', 'line-width': 2, 'line-dasharray': [3,2]}});
        map.addSource('selected-cell', {type: 'geojson', data: empty});
        map.addLayer({id: 'cell-outline', type: 'line', source: 'selected-cell', paint: {'line-color': '#203d35', 'line-width': 2}});
        comparison = createPlaceComparison({manifest, initialHash:hash, onShow:showSavedPlace, onChange:showSavedMarkers});
        showSavedMarkers();
        updateLayer(); chooseSite();
        if (savedPoint?.length === 2 && savedPoint.every(Number.isFinite) && Math.abs(savedPoint[0])<=180 && Math.abs(savedPoint[1])<=90) findPoint(savedPoint,'Saved point',++navigation);
        if (hash.has('v') && hash.get('v') !== manifest.release_id) message('This saved link used a different pilot release. The current verified release is shown.');
        $('site').disabled = false;
        $('sources').disabled = false; $('metrics').disabled = false; $('recenter').disabled = false;
        $('place').disabled = false; $('search-button').disabled = false;
        $('pilot-search').addEventListener('submit', search);
        $('site').addEventListener('change', () => {++navigation; $('search-results').hidden=true; $('search-status').textContent='Available within the four preview areas.'; site = $('site').value; location = null; updateLayer(); chooseSite();});
        $('sources').addEventListener('change', e => {source = e.target.value; updateLayer();});
        $('show-aircraft').addEventListener('click', () => { source = 'aircraft'; document.querySelector('input[name="source"][value="aircraft"]').checked = true; updateLayer(); });
        $('metrics').addEventListener('change', e => {metric = e.target.value; updateLayer();});
        $('overlay').addEventListener('change', () => map.setLayoutProperty('noise', 'visibility', $('overlay').checked ? 'visible' : 'none'));
        $('recenter').addEventListener('click', recenter);
        map.on('click', e => {++navigation; $('search-results').hidden=true; inspect([e.lngLat.lng, e.lngLat.lat]);});
      });
    } catch {
      message('The pilot could not be loaded. Return to the England map, or restart the app with the launcher.');
      $('point-value').textContent = 'Pilot unavailable';
    }
  }
  start();
})();
