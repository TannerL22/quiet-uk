/* Quiet UK explorer: map display and analytical evidence stay separate. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let map, dataset, marker, selected, selectionTitle, selectedPoint, sourceView = 'both';
  let lookupController, searchController, searchSequence = 0, toastTimer;
  const detail = new URLSearchParams(location.hash.slice(1)).get('detail');
  if (detail) {
    const saved = new URLSearchParams(detail), sourceLabel = {road:'Road',rail:'Rail',aircraft:'Aircraft'}[saved.get('source')] || 'Source', metricLabel = {Lden:'Lden',Lday:'Day',Lnight:'Night'}[saved.get('metric')] || 'time indicator';
    $('detail-context').hidden = false;
    $('detail-context').textContent = `This overview uses historical Lden evidence with different construction. Your ${sourceLabel} · ${metricLabel} detailed view is saved in the return link.`;
  }
  if (detail) document.querySelectorAll('a[href="/pilot"]').forEach(a => { a.href = '/pilot#'+detail; a.textContent = 'Return to detailed view ↗'; });
  const empty = {type:'FeatureCollection', features:[]};

  function toast(message) {
    $('toast').textContent = message; $('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => {$('toast').hidden = true;}, 5500);
  }
  function mapMessage(message) { $('map-message').textContent = message; $('map-message').hidden = !message; }
  async function api(path, signal) {
    const response = await fetch(path, {signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The data could not be loaded.');
    return data;
  }
  function fields(target, entries) {
    target.replaceChildren();
    for (const [label, value] of entries) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = value; target.append(dt, dd);
    }
  }
  function showAbout() { $('about-dialog').showModal(); }
  ['about-button','method-button','legend-info'].forEach(id => $(id).addEventListener('click', showAbout));
  $('close-about').addEventListener('click', () => $('about-dialog').close());
  $('about-dialog').addEventListener('click', event => {if(event.target === $('about-dialog')) {const r = event.target.getBoundingClientRect(); if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) event.target.close();}});

  function saveView() {
    if (!map || !dataset) return;
    const c = map.getCenter();
    const hash = new URLSearchParams({v:dataset.release_id, map:`${c.lat.toFixed(5)},${c.lng.toFixed(5)},${map.getZoom().toFixed(2)}`, view:sourceView, noise:$('noise-toggle').checked?'1':'0', opacity:$('opacity').value});
    if (detail) hash.set('detail',detail);
    if (selectedPoint) hash.set('point', `${selectedPoint[1].toFixed(6)},${selectedPoint[0].toFixed(6)}`);
    history.replaceState(null, '', '#'+hash.toString());
  }
  $('share-button').addEventListener('click', async () => {
    saveView();
    try {await navigator.clipboard.writeText(location.href); toast('View link copied. This local preview link works on this computer.');}
    catch {toast('Copy the address bar to share this view. Local preview links work on this computer.');}
  });
  function clearSelection() {
    lookupController?.abort(); selected = null; selectedPoint = null;
    marker?.remove(); marker = null;
    map?.getSource('selected-cell')?.setData(empty);
    $('location-panel').hidden = true; $('welcome').hidden = false;
    $('return-selection').hidden = true;
    saveView();
  }
  $('clear-selection').addEventListener('click', clearSelection);
  $('return-selection').addEventListener('click', () => {
    if (!map || !selectedPoint) return;
    map.easeTo({center:selectedPoint, zoom:Math.max(map.getZoom(),12), duration:reducedMotion?0:650});
  });

  function renderLocation(result) {
    const band = result.bands.road_rail_upper_db;
    const value = band.value;
    const missing = value === null;
    $('noise-card').style.background = missing ? '#eeeee8' : '#e9efe2';
    $('noise-value').textContent = missing ? 'Evidence unavailable' : result.at_reporting_floor ? 'Below reporting detail' : `Up to ${Math.ceil(value)} dB`;
    $('noise-summary').textContent = missing ? (result.land_status !== 'england_land' ? 'This location is outside the current England land dataset. An uncoloured map does not mean silence.' : 'The source checks do not support a qualified road/rail value here. It has been withheld.') : result.at_reporting_floor ? 'The source maps cannot distinguish the quieter places in this group. This is a reporting limit, not a measurement of how quiet it feels.' : 'A conservative bound on mapped road and rail noise. Actual conditions and other sound sources may differ.';
    $('noise-chip').textContent = missing ? 'No qualified value' : 'Lden · long-term indicator';
    const airport = result.bands.airport_reported_fraction;
    const aircraftLevel = result.bands.airport_reported_lower_db;
    const aircraftAvailable = aircraftLevel.qualification === 'qualified' && aircraftLevel.value !== null;
    $('aircraft-card').hidden = !aircraftAvailable;
    $('aircraft-value').textContent = aircraftAvailable ? `At least ${(Math.floor(aircraftLevel.value*10)/10).toFixed(1)} dB` : '';
    let aviationTitle, aviationSummary;
    if (airport.qualification === 'withheld_due_to_source_quality') {
      aviationTitle = 'Aircraft evidence withheld'; aviationSummary = 'Source-grid checks did not pass here. Aircraft noise has not been ruled out.';
    } else if (airport.qualification === 'qualified_with_coverage_limitation' || airport.value === null) {
      aviationTitle = 'Aircraft coverage is incomplete'; aviationSummary = 'This dataset does not establish aircraft exposure here.';
    } else if (airport.value > 0) {
      aviationTitle = 'Mapped aircraft contribution present'; aviationSummary = sourceView === 'road_rail' ? 'Aircraft evidence is hidden in this map view. Switch to Together or Aircraft to see it.' : 'Purple hatching in Together marks reported aircraft noise. Aircraft view shows its lower-bound level.';
    } else {
      aviationTitle = 'Aircraft noise is not ruled out'; aviationSummary = 'No airport pixels were reported here. That is not evidence of no aircraft noise.';
    }
    $('aviation-title').textContent = aviationTitle; $('aviation-summary').textContent = aviationSummary;
    $('resolution-title').textContent = result.cell ? 'A 100 m view' : 'England coverage';
    $('resolution-summary').textContent = result.cell ? 'The outlined cell is the exact area inspected. It is not an individual home or a sound reading right now.' : 'Noise evidence for the rest of the UK is planned. No exposure value is assigned here.';
    const exact = missing ? 'Not available' : `${value.toFixed(4)} dB (stored bound)`;
    fields($('evidence-fields'), [
      ['Road/rail bound', exact], ['Qualification', band.qualification.replaceAll('_',' ')],
      ['Aircraft lower bound', aircraftAvailable ? `${aircraftLevel.value.toFixed(4)} dB (stored bound)` : 'Not available'],
      ['Aircraft qualification', aircraftLevel.qualification.replaceAll('_',' ')],
      ['Reported aircraft spatial fraction', airport.value === null ? 'Not available' : `${(airport.value*100).toFixed(1)}% of fine pixels (not time)`],
      ['Source period', 'Not established for this historical release'], ['Metric linkage', 'Lden configured; historical acquisition unresolved'],
      ['Grid', '100 m · British National Grid'], ['Tile', result.tile_id || 'None'],
      ['Dataset', result.dataset.release_id], ['Research status', 'Provisional historical baseline']
    ]);
    $('location-details').hidden = false;
    const query = `lon=${selectedPoint[0]}&lat=${selectedPoint[1]}`;
    $('download-location').href = '/downloads/location.json?'+query;
    $('view-location-json').href = '/api/location?'+query;
    map.getSource('selected-cell').setData(result.cell_geojson || empty);
  }

  async function inspect(lon, lat, name='Selected location') {
    if (!map?.getSource('selected-cell')) return;
    lookupController?.abort(); lookupController = new AbortController();
    const signal = lookupController.signal;
    selected = null; selectedPoint = [lon, lat]; selectionTitle = name;
    $('return-selection').hidden = false;
    $('welcome').hidden = true; $('location-panel').hidden = false;
    $('location-title').textContent = name;
    $('location-coordinates').textContent = `${lat.toFixed(5)}° N, ${Math.abs(lon).toFixed(5)}° ${lon < 0 ? 'W' : 'E'}`;
    $('location-loading').hidden = false; $('location-details').hidden = true; $('location-error').hidden = true;
    map.getSource('selected-cell').setData(empty);
    if (!marker) {const el = document.createElement('div'); el.className = 'selection-pin'; marker = new maplibregl.Marker({element:el});}
    marker.setLngLat([lon, lat]).addTo(map);
    $('sidebar-scroll').scrollTop = 0; saveView();
    try {
      const result = await api(`/api/location?lon=${lon}&lat=${lat}`, signal);
      if(signal.aborted) return;
      selected = result; renderLocation(result);
      if (innerWidth <= 700) toast('Location evidence is below the map.');
    } catch(error) {
      if(signal.aborted) return;
      $('location-error').textContent = error.message; $('location-error').hidden = false;
    } finally {if(!signal.aborted) $('location-loading').hidden = true;}
  }
  $('inspect-center').addEventListener('click', () => {if(map) {const c=map.getCenter(); inspect(c.lng,c.lat);}});
  $('england-button').addEventListener('click', () => {if(map) map.fitBounds([[-5.8,49.9],[1.9,55.85]], {padding:65,duration:reducedMotion?0:1000});});
  document.querySelectorAll('.place-preset').forEach(button => button.addEventListener('click', () => {
    if(!map) return; clearSelection();
    map.flyTo({center:[Number(button.dataset.lon),Number(button.dataset.lat)],zoom:Number(button.dataset.zoom),duration:reducedMotion?0:1300});
  }));
  function updateView() {
    const active = sourceView === 'both' ? ['road_rail', 'aircraft_presence'] : [sourceView];
    for (const mode of ['road_rail', 'aircraft', 'aircraft_presence']) {
      if (map?.getLayer(mode)) {
        map.setLayoutProperty(mode, 'visibility', $('noise-toggle').checked && active.includes(mode) ? 'visible' : 'none');
        map.setPaintProperty(mode, 'raster-opacity', Number($('opacity').value)/100);
      }
    }
    $('map-view-title').textContent = {both:'Transport evidence', road_rail:'Road & rail evidence', aircraft:'Aircraft evidence'}[sourceView];
    $('legend-metric').textContent = sourceView === 'aircraft' ? 'Aircraft reported lower bound · dB Lden' : 'Road/rail upper bound · dB Lden';
    $('aircraft-key').hidden = sourceView !== 'both';
    $('unreported-key').hidden = sourceView !== 'aircraft';
    $('view-explanation').textContent = {
      both:'Separate evidence, not a combined level. No hatching does not rule out aircraft noise.',
      road_rail:'Aircraft is hidden. These colours do not describe overall noise.',
      aircraft:'Reported long-term aircraft evidence, not event peaks. Other sources are hidden.'
    }[sourceView];
    if (selected) renderLocation(selected);
    saveView();
  }
  document.querySelectorAll('input[name="source-view"]').forEach(input => input.addEventListener('change', () => {sourceView=input.value; updateView();}));
  $('noise-toggle').addEventListener('change', updateView);
  $('opacity').addEventListener('input', updateView);

  $('search-form').addEventListener('submit', async event => {
    event.preventDefault(); const query = $('search-input').value.trim();
    if(!query) return;
    if(!map?.getSource('selected-cell')) { $('search-status').textContent='The map is still starting. Please try again in a moment.'; return; }
    const coords = query.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
    searchController?.abort(); searchController = new AbortController();
    const sequence = ++searchSequence;
    $('search-results').replaceChildren(); $('search-results').hidden = true;
    if(coords) {
      const lat=Number(coords[1]),lon=Number(coords[2]);
      if(lat<49 || lat>61 || lon< -11 || lon>3) { $('search-status').textContent='Use UK coordinates in latitude, longitude order.'; return; }
      if(map) {map.flyTo({center:[lon,lat],zoom:12,duration:reducedMotion?0:800}); inspect(lon,lat);}
      $('search-status').textContent='Coordinates selected. No external search was needed.'; return;
    }
    $('search-status').textContent = 'Finding places…';
    try {
      const {places} = await api('/api/search?q='+encodeURIComponent(query), searchController.signal);
      if(sequence !== searchSequence) return;
      $('search-status').textContent = places.length ? 'Choose a place · OpenStreetMap' : 'No matching places. Try a nearby town or latitude, longitude.';
      for(const place of places) {
        const button = document.createElement('button'); button.type='button'; button.textContent=place.name;
        button.addEventListener('click', () => {
          $('search-results').hidden=true; $('search-input').value=place.name.split(',')[0];
          $('search-status').textContent='Place search by OpenStreetMap';
          map.flyTo({center:[place.longitude,place.latitude],zoom:12,duration:reducedMotion?0:1000});
          inspect(place.longitude,place.latitude,place.name.split(',')[0]);
        });
        $('search-results').append(button);
      }
      $('search-results').hidden = !places.length;
    } catch(error) {if(sequence === searchSequence && error.name !== 'AbortError') $('search-status').textContent=error.message;}
  });

  async function start() {
    try {
      dataset = await api('/api/dataset');
      fields($('dataset-facts'), [['Coverage','England land'],['Resolution','100 m analytical grid'],['Metric','Lden (historical linkage unresolved)'],['Reference periods','Not established'],['Dataset',dataset.release_id],['Catalogue',dataset.catalogue_build_id]]);
      const params = new URLSearchParams(location.hash.slice(1));
      sourceView = ['both','road_rail','aircraft'].includes(params.get('view')) ? params.get('view') : 'both';
      document.querySelector(`input[name="source-view"][value="${sourceView}"]`).checked = true;
      let center=[-1.783,53.256],zoom=9.7;
      const initial = (params.get('map')||'').split(',').map(Number);
      if(initial.length===3 && initial.every(Number.isFinite) && initial[0]>=49 && initial[0]<=61 && initial[1]>=-11 && initial[1]<=3) {center=[initial[1],initial[0]];zoom=Math.max(5,Math.min(16,initial[2]));}
      $('noise-toggle').checked = params.get('noise') !== '0';
      if(params.has('opacity')) $('opacity').value=String(Math.max(15,Math.min(95,Number(params.get('opacity'))||72)));
      if(params.has('v') && params.get('v') !== dataset.release_id) mapMessage('This link refers to another dataset version. Showing the available release; comparisons may differ.');
      let style;
      try {const response=await fetch('https://tiles.openfreemap.org/styles/positron',{signal:AbortSignal.timeout(10000)}); if(!response.ok) throw new Error(); style=await response.json();}
      catch {style={version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#e4e9e2'}}]}; mapMessage('The geographic basemap is unavailable. Noise data still loads; try coordinates or refresh when online.');}
      map = new maplibregl.Map({container:'map',style,center,zoom,minZoom:5,maxZoom:16,maxBounds:[[-11,49],[3,61]],attributionControl:true,renderWorldCopies:false,dragRotate:false,pitchWithRotate:false});
      map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
      map.addControl(new maplibregl.ScaleControl({maxWidth:90,unit:'metric'}),'bottom-right');
      map.on('error', event => {if(event.error?.message?.includes('/tiles/')) mapMessage('The noise layer could not be read. Do not interpret missing colour as quiet. Try refreshing.');});
      map.on('load', () => {
        const labels = map.getStyle().layers.find(layer => layer.type==='symbol');
        for (const mode of ['road_rail','aircraft','aircraft_presence']) {
          map.addSource(mode+'-data',{type:'raster',tiles:[`${location.origin}/tiles/${dataset.release_id}/${mode}/{z}/{x}/{y}.png?style=global-hatch-1`],tileSize:256,minzoom:5,maxzoom:16,bounds:dataset.bounds_wgs84,attribution:'Noise: configured Defra products · historical linkage unresolved'});
          map.addLayer({id:mode,type:'raster',source:mode+'-data',layout:{visibility:'none'},paint:{'raster-opacity':Number($('opacity').value)/100,'raster-resampling':'nearest','raster-fade-duration':0}}, labels?.id);
        }
        map.addSource('selected-cell',{type:'geojson',data:empty});
        map.addLayer({id:'selected-cell-outline',type:'line',source:'selected-cell',paint:{'line-color':'#294339','line-width':2,'line-dasharray':[2,1]}});
        updateView();
        map.on('click', e => inspect(e.lngLat.lng,e.lngLat.lat));
        map.on('moveend', saveView);
        const point=(params.get('point')||'').split(',').map(Number);
        if(point.length===2 && point.every(Number.isFinite) && point[0]>=49 && point[0]<=61 && point[1]>=-11 && point[1]<=3) inspect(point[1],point[0]);
        saveView();
      });
    } catch(error) {mapMessage('The explorer could not start: '+error.message); $('search-status').textContent='Map unavailable. Check the local server and refresh.';}
  }
  start();
})();
