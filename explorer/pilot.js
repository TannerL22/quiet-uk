(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {road: 'Road', rail: 'Rail', aircraft: 'Aircraft'};
  const metrics = ['Lden', 'Lday', 'Lnight'];
  const labels = {Lden: 'Daily average · Lden', Lday: 'Day · 07:00–19:00', Lnight: 'Night · 23:00–07:00'};
  let manifest, map, marker, location, selection = 0, navigation = 0;
  let site = 'heathrow', source = 'aircraft', metric = 'Lden';
  let point, comparison, capabilities;
  let savedMarkers = [];
  let noiseLayers = [];
  let renderedLayer;
  const empty = {type: 'FeatureCollection', features: []};
  const message = text => { $('message').textContent = text; $('message').hidden = !text; };
  const records = () => manifest.records.filter(r => r.site === site && r.source === source && r.metric === metric);
  const feature = geometry => ({type: 'Feature', properties: {}, geometry});
  const areaSize = () => (manifest.sites[site].size_m || 2000)/1000;
  // Pasting another comparison link into this same tab can change only the
  // fragment. Reload to validate its release/recipe just like a fresh visit.
  // Our own replaceState updates do not fire hashchange.
  document.querySelectorAll('a[href="#inspect-centre"], a[href="#point-title"]').forEach(a => a.addEventListener('click', event => {
    event.preventDefault(); const target = document.querySelector(a.getAttribute('href'));
    target.focus(); target.scrollIntoView({block:'center'});
  }));
  window.addEventListener('hashchange', () => window.location.reload());
  function saveView() {
    const params = new URLSearchParams({site, source, metric, v: manifest.release_id, noise:$('overlay').checked?'1':'0'});
    if (map) { const c = map.getCenter(); params.set('camera', [c.lng.toFixed(6),c.lat.toFixed(6),map.getZoom().toFixed(2)].join(',')); }
    if (point) params.set('point', point.map(x => x.toFixed(6)).join(','));
    const places = comparison?.getPlaces() || [];
    if (places.length) params.set('compare', JSON.stringify(places));
    history.replaceState(null, '', '#'+params.toString());
    const coords = point || manifest.sites[site].center;
    $('england-map').href = '/overview#'+new URLSearchParams({map:`${coords[1]},${coords[0]},12`,point:`${coords[1]},${coords[0]}`,view:'both',detail:params.toString()});
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
    const selected = records(), p = manifest.products[source];
    const layerKey = `${site}/${source}/${metric}`;
    // Draw underneath geographic labels, but above the basemap's ground layers.
    const before = map.getStyle().layers.find(l => l.type === 'symbol')?.id || 'pilot-outline';
    if (renderedLayer !== layerKey) {
      noiseLayers.forEach(id => { map.removeLayer(id); map.removeSource(id); });
      noiseLayers = [];
      for (const r of selected) {
      const id = 'noise-'+r.id;
      map.addSource(id, {type:'image', url:`/pilot-images/${manifest.release_id}/${r.id}.png`, coordinates:r.display.corners});
      map.addLayer({id, type:'raster', source:id, layout:{visibility:$('overlay').checked?'visible':'none'}, paint:{'raster-opacity':.85, 'raster-resampling':'nearest', 'raster-fade-duration':0}}, before);
        noiseLayers.push(id);
      }
      renderedLayer = layerKey;
    }
    $('legend-title').textContent = `${names[source]} · ${labels[metric]} · dB(A)`;
    $('source-scope').textContent = `${names[source]} only · other sources are excluded. This is not overall sound exposure.`;
    $('unknown-legend').replaceChildren();
    for (const item of manifest.evidence_contract?.display_missing || [{label:'Unreported / unknown', colour:'#c0b8a5'}]) {
      const span = document.createElement('span'), swatch = document.createElement('i');
      swatch.style.backgroundColor = item.colour; span.append(swatch, document.createTextNode(item.label)); $('unknown-legend').append(span);
    }
    $('metric-note').textContent = metric === 'Lden' ? '*Lden: annual daily energy average, with added weighting for evening and night. It does not describe background sound or individual loud events.' : manifest.metrics[metric];
    $('period').textContent = p.reference_period ? `Reference period: ${p.reference_period.start.slice(0,4)} · 10 m grid · 4 m above ground` : 'Aircraft reference period: unspecified by provider · 10 m grid';
    $('provider').href = 'https://environment.data.gov.uk/dataset/'+p.metadata_id;
    $('map-title').textContent = `${names[source]} · ${labels[metric]}`;
    $('area-size').textContent = `${areaSize()} × ${areaSize()} km`;
    map.getSource('pilot-area').setData(feature(manifest.sites[site].footprint || selected[0].footprint));
    map.getSource('all-areas').setData({type:'FeatureCollection', features:Object.entries(manifest.sites).map(([id, area]) => ({...feature(area.footprint || manifest.records.find(r=>r.site===id).footprint),properties:{name:area.name}}))});
    renderPoint();
    saveView();
  }
  function recenter() {
    const corners = records().flatMap(r => r.display.corners);
    const bounds = new maplibregl.LngLatBounds();
    corners.forEach(c => bounds.extend(c));
    map.fitBounds(bounds, {padding: {top: 110, bottom: 160, left: 30, right: 30}, duration: 0, maxZoom: 15});
  }
  function chooseSite() {
    recenter();
    inspect(manifest.sites[site].center);
  }
  async function findPoint(coords, label, ticket, move = true) {
    ++selection; location = null; comparison?.update(null, source, metric);
    $('point-download').hidden = true; $('aircraft-cue').hidden = true;
    $('point-value').textContent = 'Checking coverage…';
    $('point-status').textContent = ''; $('comparison').replaceChildren();
    map.getSource('selected-cell').setData(empty);
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
        $('search-status').textContent = `${label} is outside the available detailed areas. No noise level is available from this release here. Use Show all areas to explore coverage.`;
      }
      if (move) map.jumpTo({center:coords,zoom:14.5});
      await inspect(coords);
    } catch {
      if (ticket === navigation) { $('search-status').textContent = 'Coverage could not be checked. Try again or select an area.'; $('point-value').textContent = 'Coverage unavailable'; }
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
      const app = await fetch('/api/app', {signal: AbortSignal.timeout(15000)});
      if (!app.ok) throw new Error(); capabilities = await app.json();
      let hash = new URLSearchParams(window.location.hash.slice(1));
      if (window.location.pathname === '/' && hash.has('map') && !hash.has('site')) {
        if (capabilities.overview_release) { window.location.replace('/overview'+window.location.hash); return; }
        message('This link refers to the historical overview, which is not installed. Available detailed areas are shown instead.'); hash = new URLSearchParams();
      }
      $('overview-note').textContent = capabilities.overview_release ? 'Wider coverage is available in the separate, provisional historical overview.' : 'Detailed coverage only; the historical overview is not installed.';
      const response = await fetch('/api/pilot', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error();
      manifest = await response.json();
      if (manifest.schema_version === 3) source = 'road';
      if (manifest.schema_version === 3 || !manifest.sites[site]) site = Object.keys(manifest.sites)[0];
      if (manifest.sites[hash.get('site')]) site = hash.get('site');
      if (Object.hasOwn(names, hash.get('source'))) source = hash.get('source');
      if (metrics.includes(hash.get('metric'))) metric = hash.get('metric');
      const savedPoint = hash.get('point')?.split(',').map(Number);
      const camera = hash.get('camera')?.split(',').map(Number);
      $('overlay').checked = hash.get('noise') !== '0';
      for (const [id, s] of Object.entries(manifest.sites)) {
        const option = document.createElement('option'); option.value = id; option.textContent = s.name; $('site').append(option);
      }
      $('site').value = site;
      const areaCount = Object.keys(manifest.sites).length;
      const areaTotal = Object.values(manifest.sites).reduce((sum,s)=>sum+((s.size_m||2000)/1000)**2,0);
      $('coverage-title').textContent = `${areaCount} detailed areas · ${areaTotal} km²`;
      $('coverage-note').textContent = 'Outlines mark available areas. Outside them, no detailed noise level is available.';
      $('search-status').textContent = `Detailed evidence is available in ${areaCount} areas.`;
      $('intro').textContent = 'Explore modelled transport noise by source and time of day.';
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
      map.on('error', e => {if (e.sourceId?.startsWith('noise-')) message('A noise overlay could not be loaded. Point inspection still provides verified values.');});
      map.on('load', async () => {
        map.addSource('all-areas', {type:'geojson',data:empty});
        map.addLayer({id:'coverage-fill',type:'fill',source:'all-areas',paint:{'fill-color':'#258b92','fill-opacity':.06}});
        map.addLayer({id:'coverage-outline',type:'line',source:'all-areas',paint:{'line-color':'#258b92','line-width':2}});
        map.addSource('pilot-area', {type: 'geojson', data: empty});
        map.addLayer({id: 'pilot-outline', type: 'line', source: 'pilot-area', paint: {'line-color': '#506d51', 'line-width': 2, 'line-dasharray': [3,2]}});
        map.addSource('selected-cell', {type: 'geojson', data: empty});
        map.addLayer({id: 'cell-outline', type: 'line', source: 'selected-cell', paint: {'line-color': '#203d35', 'line-width': 2}});
        comparison = createPlaceComparison({manifest, initialHash:hash, onShow:showSavedPlace, onChange:showSavedMarkers});
        showSavedMarkers();
        updateLayer(); recenter();
        if (savedPoint?.length === 2 && savedPoint.every(Number.isFinite) && Math.abs(savedPoint[0])<=180 && Math.abs(savedPoint[1])<=90) await findPoint(savedPoint,'Saved point',++navigation);
        else await inspect(manifest.sites[site].center);
        if (camera?.length === 3 && camera.every(Number.isFinite) && Math.abs(camera[0]) <= 180 && Math.abs(camera[1]) <= 85 && camera[2] >= 0 && camera[2] <= 20) map.jumpTo({center:camera.slice(0,2),zoom:camera[2]});
        saveView(); map.on('moveend', saveView);
        $('england-map').hidden = !capabilities.overview_release;
        if (hash.has('v') && hash.get('v') !== manifest.release_id) message('This saved link used a different pilot release. The current verified release is shown.');
        $('site').disabled = false;
        $('sources').disabled = false; $('metrics').disabled = false; $('recenter').disabled = false;
        $('place').disabled = false; $('search-button').disabled = false;
        $('pilot-search').addEventListener('submit', search);
        $('site').addEventListener('change', () => {++navigation; $('search-results').hidden=true; $('search-status').textContent=`Detailed evidence is available in ${areaCount} areas.`; site = $('site').value; location = null; updateLayer(); chooseSite();});
        $('sources').addEventListener('change', e => {source = e.target.value; updateLayer();});
        $('show-aircraft').addEventListener('click', () => { source = 'aircraft'; document.querySelector('input[name="source"][value="aircraft"]').checked = true; updateLayer(); });
        $('metrics').addEventListener('change', e => {metric = e.target.value; updateLayer();});
        $('overlay').addEventListener('change', () => { noiseLayers.forEach(id=>map.setLayoutProperty(id, 'visibility', $('overlay').checked ? 'visible' : 'none')); saveView(); });
        $('recenter').addEventListener('click', recenter);
        $('show-coverage').disabled = false; $('inspect-centre').disabled = false; $('share-view').disabled = false;
        $('show-coverage').addEventListener('click', () => {
          const bounds = new maplibregl.LngLatBounds();
          manifest.records.filter(r => r.source === source && r.metric === metric).forEach(r => r.display.corners.forEach(c => bounds.extend(c)));
          map.fitBounds(bounds, {padding:{top:150,bottom:190,left:35,right:35},duration:0});
          $('search-status').textContent = 'Outlines show available areas. Select one on the map or choose it above. Only the selected area is coloured.';
        });
        $('inspect-centre').addEventListener('click', () => { const c = map.getCenter(); findPoint([c.lng,c.lat],'Map centre',++navigation,false); });
        $('share-view').addEventListener('click', async () => {
          saveView();
          try { await navigator.clipboard.writeText(window.location.href); $('share-status').textContent = 'View link copied. This local link works on this computer.'; }
          catch { $('share-status').textContent = 'Copy the address bar to share this view on this computer.'; }
        });
        map.on('click', e => { $('search-results').hidden=true; findPoint([e.lngLat.lng, e.lngLat.lat],'Selected point',++navigation,false); });
      });
    } catch {
      message('Detailed evidence could not be loaded. Restart with the launcher or provide a verified regional bundle.');
      $('point-value').textContent = 'Pilot unavailable';
    }
  }
  start();
})();
