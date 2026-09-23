(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const LIST_PAGE_SIZE = 50;
  const state = {
    report: null,
    geojson: null,
    bng: null,
    byId: new Map(),
    indexById: new Map(),
    pathById: new Map(),
    selectedId: null,
    selectedPath: null,
    selectedPoint: null,
    view: null,
    listPage: 0,
    renderToken: 0,
  };
  const palette = ["#766b87", "#6e7b86", "#92745f", "#667b75", "#8a6f83", "#82755e"];

  const $ = (id) => document.getElementById(id);
  const number = (value, digits = 0) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits }).format(value);
  };
  const db = (value) => `${number(value, 2)} dB`;
  const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[character]));

  function fail(message) {
    $("header-status").textContent = "Unable to display candidate outputs";
    $("error").textContent = message;
    $("error").hidden = false;
    $("app").hidden = true;
  }

  function finite(value, label) {
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} must be a finite number`);
  }

  function validateRing(ring, label) {
    if (!Array.isArray(ring) || ring.length < 4) throw new Error(`${label} must have at least four coordinate pairs`);
    ring.forEach((pair, index) => {
      if (!Array.isArray(pair) || pair.length < 2) throw new Error(`${label}[${index}] is not a coordinate pair`);
      finite(pair[0], `${label}[${index}][0]`);
      finite(pair[1], `${label}[${index}][1]`);
    });
  }

  function validateGeometry(geometry, label) {
    if (!geometry || typeof geometry !== "object") throw new Error(`${label} must be a geometry object`);
    if (geometry.type === "Polygon") {
      if (!Array.isArray(geometry.coordinates) || !geometry.coordinates.length) throw new Error(`${label} Polygon has no rings`);
      geometry.coordinates.forEach((ring, index) => validateRing(ring, `${label}.coordinates[${index}]`));
    } else if (geometry.type === "MultiPolygon") {
      if (!Array.isArray(geometry.coordinates) || !geometry.coordinates.length) throw new Error(`${label} MultiPolygon has no polygons`);
      geometry.coordinates.forEach((polygon, polygonIndex) => {
        if (!Array.isArray(polygon) || !polygon.length) throw new Error(`${label}.coordinates[${polygonIndex}] has no rings`);
        polygon.forEach((ring, ringIndex) => validateRing(ring, `${label}.coordinates[${polygonIndex}][${ringIndex}]`));
      });
    } else {
      throw new Error(`${label} geometry type must be Polygon or MultiPolygon`);
    }
  }

  function visitGeometryCoordinates(geometry, visit) {
    if (geometry.type === "Polygon") {
      geometry.coordinates.forEach((ring) => ring.forEach((point) => visit(point)));
    } else {
      geometry.coordinates.forEach((polygon) => polygon.forEach((ring) => ring.forEach((point) => visit(point))));
    }
  }

  function validateComponent(component, index) {
    if (!component || typeof component.component_id !== "string" || !component.component_id.trim()) throw new Error(`component ${index} has no valid component_id`);
    if (!Number.isInteger(component.area_order) || component.area_order !== index + 1) throw new Error(`component ${component.component_id} has invalid area_order`);
    if (!Array.isArray(component.bounds_bng) || component.bounds_bng.length !== 4) throw new Error(`component ${component.component_id} has malformed bounds_bng`);
    component.bounds_bng.forEach((value, boundIndex) => finite(value, `component ${component.component_id} bounds_bng[${boundIndex}]`));
    if (!Number.isInteger(component.cell_count) || component.cell_count <= 0) throw new Error(`component ${component.component_id} has invalid cell_count`);
    finite(component.area_km2, `component ${component.component_id} area_km2`);
    if (!component.representative_cell || !component.representative_cell.center_bng) throw new Error(`component ${component.component_id} has no representative point`);
    finite(component.representative_cell.center_bng.easting_m, `component ${component.component_id} representative easting`);
    finite(component.representative_cell.center_bng.northing_m, `component ${component.component_id} representative northing`);
    if (!component.road_rail_upper_db || !Number.isFinite(component.road_rail_upper_db.min) || !Number.isFinite(component.road_rail_upper_db.max) || !Number.isFinite(component.road_rail_upper_db.requested_threshold_db)) throw new Error(`component ${component.component_id} has malformed road/rail upper detail`);
    if (!component.airport || typeof component.airport !== "object") throw new Error(`component ${component.component_id} has no airport detail`);
    if (component.airport.cell_count !== component.cell_count) throw new Error(`component ${component.component_id} airport detail does not reconcile to cell_count`);
    if (!Array.isArray(component.source_tile_ids) || component.source_tile_ids.some((tileId) => typeof tileId !== "string")) throw new Error(`component ${component.component_id} has malformed source_tile_ids`);
    ["touches_requested_bbox_boundary", "adjoins_uncovered_land", "adjoins_road_rail_withheld_land"].forEach((field) => {
      if (typeof component[field] !== "boolean") throw new Error(`component ${component.component_id} has malformed ${field}`);
    });
  }

  function validateJoin(report, geojson, bng) {
    if (!report || typeof report !== "object") throw new Error("candidates.json must be a JSON object");
    if (!geojson || typeof geojson !== "object") throw new Error("candidates.geojson must be a JSON object");
    if (!bng || typeof bng !== "object") throw new Error("derived BNG payload must be a JSON object");
    if (typeof report.run_id !== "string" || !report.run_id.trim()) throw new Error("candidates.json has no valid run_id");
    if (geojson.type !== "FeatureCollection") throw new Error("candidates.geojson must be a GeoJSON FeatureCollection");
    if (geojson.run_id !== report.run_id) throw new Error(`run_id mismatch: JSON ${JSON.stringify(report.run_id)} vs GeoJSON ${JSON.stringify(geojson.run_id)}`);
    if (bng.type !== "FeatureCollection" || bng.display_crs !== "EPSG:27700" || bng.source_crs !== "EPSG:4326") throw new Error("derived BNG payload has an unexpected coordinate reference system");
    if (bng.run_id !== report.run_id) throw new Error("run_id mismatch: JSON and derived BNG payload");
    if (!Array.isArray(report.components) || !Array.isArray(geojson.features) || !Array.isArray(bng.features)) throw new Error("all outputs must contain arrays");
    if (!report.summary || report.summary.retained_component_count !== report.components.length) throw new Error("summary retained_component_count disagrees with components");
    if (!report.parameters || !Array.isArray(report.parameters.bbox_bng) || report.parameters.bbox_bng.length !== 4) throw new Error("screening parameters have no valid bbox_bng");
    report.parameters.bbox_bng.forEach((value, index) => finite(value, `parameters.bbox_bng[${index}]`));
    const [west, south, east, north] = report.parameters.bbox_bng;
    if (!(west < east && south < north)) throw new Error("parameters.bbox_bng must be ordered west < east and south < north");
    if (JSON.stringify(bng.bbox_bng) !== JSON.stringify(report.parameters.bbox_bng)) throw new Error("bbox_bng mismatch between JSON and derived BNG payload");
    finite(report.parameters.road_rail_upper_threshold_db, "parameters.road_rail_upper_threshold_db");
    if (!Number.isInteger(report.parameters.minimum_component_cells) || report.parameters.minimum_component_cells <= 0) throw new Error("parameters.minimum_component_cells must be a positive integer");
    if (!Number.isInteger(report.summary.requested_cells) || report.summary.requested_cells < 0) throw new Error("summary.requested_cells must be a non-negative integer");
    if (!Number.isInteger(report.summary.retained_component_cells) || report.summary.retained_component_cells < 0) throw new Error("summary.retained_component_cells must be a non-negative integer");

    const componentIds = [];
    const componentIdSet = new Set();
    report.components.forEach((component, index) => {
      validateComponent(component, index);
      if (componentIdSet.has(component.component_id)) throw new Error(`duplicate component_id in candidates.json: ${component.component_id}`);
      componentIds.push(component.component_id);
      componentIdSet.add(component.component_id);
    });

    function validateFeatureIds(features, label) {
      const ids = [];
      const idSet = new Set();
      features.forEach((feature, index) => {
        if (!feature || feature.type !== "Feature" || !feature.properties || typeof feature.properties.component_id !== "string") throw new Error(`${label} feature ${index} is malformed`);
        const id = feature.properties.component_id;
        if (idSet.has(id)) throw new Error(`duplicate component_id in ${label}: ${id}`);
        idSet.add(id);
        ids.push(id);
        validateGeometry(feature.geometry, `${label} feature ${index}`);
      });
      return { ids, idSet };
    }

    const sourceFeatures = validateFeatureIds(geojson.features, "candidates.geojson");
    if (componentIds.length !== sourceFeatures.ids.length || componentIds.some((id, index) => id !== sourceFeatures.ids[index])) {
      throw new Error(`component_id mismatch between outputs (JSON ${componentIds.length}, GeoJSON ${sourceFeatures.ids.length})`);
    }
    const bngFeatures = validateFeatureIds(bng.features, "derived BNG");
    if (componentIds.length !== bngFeatures.ids.length || componentIds.some((id, index) => id !== bngFeatures.ids[index])) {
      throw new Error(`component_id mismatch between JSON and derived BNG payload (JSON ${componentIds.length}, BNG ${bngFeatures.ids.length})`);
    }
    bng.features.forEach((feature, index) => {
      const center = feature.properties.representative_center_bng;
      if (!center || typeof center !== "object") throw new Error(`derived BNG feature ${index} has no representative point`);
      finite(center.easting_m, `derived BNG feature ${index} representative easting`);
      finite(center.northing_m, `derived BNG feature ${index} representative northing`);
      const expected = report.components[index].representative_cell.center_bng;
      if (center.easting_m !== expected.easting_m || center.northing_m !== expected.northing_m) throw new Error(`derived BNG feature ${index} representative point does not match candidates.json`);
    });
    if (geojson.schema_version !== report.schema_version || bng.schema_version !== report.schema_version) throw new Error("schema_version mismatch between candidate outputs");
    if (!report.screening_policy || geojson.screening_policy_version !== report.screening_policy.version || bng.screening_policy_version !== report.screening_policy.version) throw new Error("screening policy version mismatch between candidate outputs");
  }

  function mapViewport() {
    const rect = $("map").getBoundingClientRect();
    return {
      width: Math.max(1, Math.round(rect.width)),
      height: Math.max(1, Math.round(rect.height)),
    };
  }

  function makeView(report, bng, viewport = mapViewport()) {
    const [west, south, east, north] = report.parameters.bbox_bng;
    const width = viewport.width;
    const height = viewport.height;
    const padding = Math.min(48, Math.max(18, Math.min(width, height) * 0.08));
    const availableWidth = Math.max(1, width - (padding * 2));
    const availableHeight = Math.max(1, height - (padding * 2));
    const scale = Math.min(availableWidth / (east - west), availableHeight / (north - south));
    const contentWidth = (east - west) * scale;
    const contentHeight = (north - south) * scale;
    const offsetX = (width - contentWidth) / 2;
    const offsetY = (height - contentHeight) / 2;

    // Walk the derived geometry once to keep bounds validation linear even for
    // fragmented inputs. The requested BNG extent remains the display extent.
    const geometryBounds = { minEasting: Infinity, maxEasting: -Infinity, minNorthing: Infinity, maxNorthing: -Infinity };
    bng.features.forEach((feature) => visitGeometryCoordinates(feature.geometry, (point) => {
      geometryBounds.minEasting = Math.min(geometryBounds.minEasting, point[0]);
      geometryBounds.maxEasting = Math.max(geometryBounds.maxEasting, point[0]);
      geometryBounds.minNorthing = Math.min(geometryBounds.minNorthing, point[1]);
      geometryBounds.maxNorthing = Math.max(geometryBounds.maxNorthing, point[1]);
    }));

    return {
      width,
      height,
      padding,
      scale,
      west,
      south,
      east,
      north,
      geometryBounds,
      project(point) {
        return [offsetX + (point[0] - west) * scale, offsetY + (north - point[1]) * scale];
      },
    };
  }

  function project(point) {
    return state.view.project(point);
  }

  function pathForRing(ring) {
    return `${ring.map((point, index) => {
      const [x, y] = project(point);
      return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ")} Z`;
  }

  function pathForGeometry(geometry) {
    const commands = [];
    if (geometry.type === "Polygon") {
      geometry.coordinates.forEach((ring) => commands.push(pathForRing(ring)));
    } else {
      geometry.coordinates.forEach((polygon) => polygon.forEach((ring) => commands.push(pathForRing(ring))));
    }
    return commands.join(" ");
  }

  function appendSvg(tag, attributes, parent) {
    const element = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    parent.appendChild(element);
    return element;
  }

  function circlePath(point, radius = 3.5) {
    const [x, y] = project(point);
    return `M${(x - radius).toFixed(2)},${y.toFixed(2)}a${radius},${radius} 0 1,0 ${(radius * 2).toFixed(2)},0a${radius},${radius} 0 1,0 ${(-radius * 2).toFixed(2)},0`;
  }

  function niceScaleDistance(view) {
    const target = 130 / view.scale;
    const exponent = Math.floor(Math.log10(Math.max(target, 1)));
    const magnitude = 10 ** exponent;
    const choices = [1, 2, 5, 10];
    let selected = magnitude;
    choices.forEach((choice) => {
      const candidate = choice * magnitude;
      if (candidate <= target) selected = candidate;
    });
    return selected;
  }

  function drawAnnotations(view) {
    const annotations = $("map-annotations");
    const northX = view.width - view.padding;
    const northY = view.padding + 8;
    appendSvg("line", { x1: northX, y1: northY + 34, x2: northX, y2: northY, class: "map-annotation-line" }, annotations);
    appendSvg("path", { d: `M${northX},${northY - 4} L${northX - 6},${northY + 8} L${northX + 6},${northY + 8} Z`, class: "map-annotation-fill" }, annotations);
    const northLabel = appendSvg("text", { x: northX - 5, y: northY - 9, class: "map-annotation-text" }, annotations);
    northLabel.textContent = "N";

    const distance = niceScaleDistance(view);
    const barPixels = distance * view.scale;
    const scaleX = view.padding;
    const scaleY = view.height - view.padding;
    appendSvg("line", { x1: scaleX, y1: scaleY, x2: scaleX + barPixels, y2: scaleY, class: "map-annotation-line" }, annotations);
    appendSvg("line", { x1: scaleX, y1: scaleY - 5, x2: scaleX, y2: scaleY + 5, class: "map-annotation-line" }, annotations);
    appendSvg("line", { x1: scaleX + barPixels, y1: scaleY - 5, x2: scaleX + barPixels, y2: scaleY + 5, class: "map-annotation-line" }, annotations);
    const scaleLabel = appendSvg("text", { x: scaleX, y: scaleY - 10, class: "map-scale-text" }, annotations);
    scaleLabel.textContent = `${number(distance)} m`;
  }

  function setMapProgress(processed, total) {
    const progress = $("map-progress");
    if (!total || processed >= total) {
      progress.hidden = true;
      progress.textContent = "";
      return;
    }
    progress.hidden = false;
    progress.textContent = `Rendering candidate footprints: ${number(processed)} of ${number(total)}`;
  }

  function nextFrame() {
    // A timer yields to layout/paint and also advances in headless virtual time.
    return new Promise((resolve) => window.setTimeout(resolve, 0));
  }

  async function drawMap() {
    const token = ++state.renderToken;
    const map = $("map");
    const requestLayer = $("request-layer");
    const footprints = $("footprints");
    const representatives = $("representatives");
    const annotations = $("map-annotations");
    requestLayer.replaceChildren();
    footprints.replaceChildren();
    representatives.replaceChildren();
    annotations.replaceChildren();
    state.pathById.clear();
    state.selectedPath = null;
    state.selectedPoint = null;
    state.view = makeView(state.report, state.bng, mapViewport());
    const view = state.view;
    map.setAttribute("viewBox", `0 0 ${view.width} ${view.height}`);
    map.dataset.displayCrs = "EPSG:27700";
    map.dataset.pixelsPerMetre = String(view.scale);
    map.dataset.bngExtent = `${view.west},${view.south},${view.east},${view.north}`;
    const background = map.querySelector(".map-background");
    background.setAttribute("width", String(view.width));
    background.setAttribute("height", String(view.height));

    const bbox = state.report.parameters.bbox_bng;
    const requestPath = appendSvg("path", {
      d: pathForRing([[bbox[0], bbox[3]], [bbox[2], bbox[3]], [bbox[2], bbox[1]], [bbox[0], bbox[1]]]),
      class: "request-outline",
    }, requestLayer);
    requestPath.setAttribute("aria-label", "Requested BNG rectangle");
    const labelPoint = project([bbox[0], bbox[3]]);
    const label = appendSvg("text", { x: labelPoint[0] + 8, y: labelPoint[1] + 20, class: "request-label" }, requestLayer);
    label.textContent = "Requested BNG extent";
    drawAnnotations(view);

    const features = state.bng.features;
    const total = features.length;
    const batchSize = total > 500 ? 250 : Math.max(total, 1);
    setMapProgress(0, total);
    for (let start = 0; start < total; start += batchSize) {
      if (token !== state.renderToken) return;
      const fragment = document.createDocumentFragment();
      const representativeCommands = [];
      const end = Math.min(start + batchSize, total);
      for (let index = start; index < end; index += 1) {
        const feature = features[index];
        const id = feature.properties.component_id;
        const component = state.byId.get(id);
        const path = appendSvg("path", {
          d: pathForGeometry(feature.geometry),
          class: `candidate-footprint${state.selectedId === id ? " selected" : ""}`,
          "data-component-id": id,
          "data-order": component.area_order,
          "fill-rule": "evenodd",
          tabindex: "0",
          role: "button",
          "aria-label": `Candidate component ${component.area_order}, ${number(component.area_km2, 2)} square kilometres`,
        }, fragment);
        path.style.setProperty("--component-colour", palette[index % palette.length]);
        path.addEventListener("click", () => select(id));
        path.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(id); }
        });
        state.pathById.set(id, path);
        const center = feature.properties.representative_center_bng;
        representativeCommands.push(circlePath([center.easting_m, center.northing_m]));
      }
      footprints.appendChild(fragment);
      if (representativeCommands.length) {
        appendSvg("path", { d: representativeCommands.join(" "), class: "representative-points", "aria-hidden": "true" }, representatives);
      }
      setMapProgress(end, total);
      if (end < total) await nextFrame();
    }
    if (token !== state.renderToken) return;
    state.selectedPoint = appendSvg("circle", { class: "selected-representative", r: "5", "aria-hidden": "true" }, representatives);
    updateSelectionVisuals();
    setMapProgress(total, total);
  }

  function drawList() {
    const list = $("component-list");
    list.replaceChildren();
    const total = state.report.components.length;
    const pageCount = Math.ceil(total / LIST_PAGE_SIZE);
    if (pageCount) state.listPage = Math.min(state.listPage, pageCount - 1);
    else state.listPage = 0;
    const start = state.listPage * LIST_PAGE_SIZE;
    const end = Math.min(start + LIST_PAGE_SIZE, total);
    for (let index = start; index < end; index += 1) {
      const component = state.report.components[index];
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "component-button";
      button.dataset.componentId = component.component_id;
      button.setAttribute("aria-pressed", String(component.component_id === state.selectedId));
      button.innerHTML = `<span class="order-number">${component.area_order}</span><span class="component-id">${escapeHtml(component.component_id)}</span><span class="component-area">${number(component.area_km2, 2)} km²</span>`;
      button.addEventListener("click", () => select(component.component_id));
      item.appendChild(button);
      list.appendChild(item);
    }
    if (!total) {
      const item = document.createElement("li");
      item.className = "component-list-empty";
      item.textContent = "No retained components in this bounded result.";
      list.appendChild(item);
    }
    $("list-count").textContent = `${number(total)} total`;
    $("list-page").textContent = pageCount ? `Page ${state.listPage + 1} of ${pageCount}` : "Page 0 of 0";
    $("list-previous").disabled = state.listPage <= 0;
    $("list-next").disabled = state.listPage >= pageCount - 1 || !pageCount;
  }

  function availabilityRows(airport) {
    const entries = Object.entries(airport.fraction_availability_by_qualification || {});
    if (!entries.length) return "<p class=\"muted\">No airport qualification rows recorded.</p>";
    return `<table class="airport-table"><thead><tr><th>Qualification</th><th>Cells</th><th>Finite / censored</th><th>Zero / positive</th></tr></thead><tbody>${entries.map(([qualification, value]) => `<tr><td>${escapeHtml(qualification)}</td><td>${number(value.cells)}</td><td>${number(value.finite)} / ${number(value.censored_or_unreported)}</td><td>${number(value.zero)} / ${number(value.positive)}</td></tr>`).join("")}</tbody></table>`;
  }

  function renderDetails(component) {
    const airport = component.airport || {};
    const flags = [
      ["Touches requested bbox boundary", component.touches_requested_bbox_boundary],
      ["Adjoins uncovered / unknown-neighbour land", component.adjoins_uncovered_land],
      ["Adjoins withheld road/rail land", component.adjoins_road_rail_withheld_land],
    ];
    $("detail-title").textContent = component.component_id;
    $("detail-order").textContent = `Area order ${component.area_order}`;
    $("detail-content").innerHTML = `
      <div class="detail-grid">
        <article class="detail-card"><h3>Cell footprint area</h3><p>${number(component.cell_count)} cells · ${number(component.area_km2, 2)} km²</p></article>
        <article class="detail-card"><h3>Road / rail upper</h3><p>${db(component.road_rail_upper_db.min)} to ${db(component.road_rail_upper_db.max)}</p></article>
        <article class="detail-card"><h3>Requested threshold</h3><p>${db(component.road_rail_upper_db.requested_threshold_db)} inclusive</p></article>
        <article class="detail-card"><h3>Representative point</h3><p>${number(component.representative_cell.center_bng.easting_m)} E, ${number(component.representative_cell.center_bng.northing_m)} N</p></article>
        <article class="detail-card detail-wide"><h3>Airport qualification and availability</h3>${availabilityRows(airport)}<p class="muted">Source states: ${Object.entries(airport.source_quality_state_counts || {}).map(([key, value]) => `${escapeHtml(key)} ${number(value)}`).join(" · ") || "—"}</p></article>
        <article class="detail-card detail-wide"><h3>Neighbour and request flags</h3><div class="detail-flags">${flags.map(([label, value]) => `<span class="flag ${value ? "flag-on" : ""}">${value ? "Yes" : "No"} · ${label}</span>`).join("")}</div></article>
        <article class="detail-card detail-wide"><h3>Source tiles</h3><p><code>${(component.source_tile_ids || []).map(escapeHtml).join(", ") || "—"}</code></p></article>
      </div>`;
  }

  function renderEmptyDetails() {
    $("detail-title").textContent = "No retained components";
    $("detail-order").textContent = "—";
    $("detail-content").innerHTML = "<p class=\"muted\">This valid bounded screen contains no components meeting the minimum size.</p>";
  }

  function updateSelectionVisuals() {
    if (state.selectedPath) state.selectedPath.classList.remove("selected");
    state.selectedPath = state.pathById.get(state.selectedId) || null;
    if (state.selectedPath) state.selectedPath.classList.add("selected");
    document.querySelectorAll(".component-button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.componentId === state.selectedId)));
    if (state.selectedPoint && state.selectedId) {
      const component = state.byId.get(state.selectedId);
      const [x, y] = project([component.representative_cell.center_bng.easting_m, component.representative_cell.center_bng.northing_m]);
      state.selectedPoint.setAttribute("cx", x.toFixed(2));
      state.selectedPoint.setAttribute("cy", y.toFixed(2));
      state.selectedPoint.removeAttribute("hidden");
    } else if (state.selectedPoint) {
      state.selectedPoint.setAttribute("hidden", "hidden");
    }
  }

  function select(id) {
    const component = state.byId.get(id);
    if (!component) return;
    state.selectedId = id;
    const index = state.indexById.get(id);
    const page = Math.floor(index / LIST_PAGE_SIZE);
    if (state.listPage !== page) {
      state.listPage = page;
      drawList();
    }
    updateSelectionVisuals();
    renderDetails(component);
  }

  async function render(report, geojson, bng) {
    state.report = report;
    state.geojson = geojson;
    state.bng = bng;
    state.byId = new Map();
    state.indexById = new Map();
    state.pathById = new Map();
    state.selectedId = null;
    state.listPage = 0;
    report.components.forEach((component, index) => {
      state.byId.set(component.component_id, component);
      state.indexById.set(component.component_id, index);
    });
    $("summary-run").textContent = report.run_id;
    $("summary-components").textContent = number(report.summary.retained_component_count);
    $("summary-cells").textContent = number(report.summary.retained_component_cells);
    $("summary-requested").textContent = number(report.summary.requested_cells);
    $("summary-threshold").textContent = db(report.parameters.road_rail_upper_threshold_db);
    $("summary-minimum").textContent = `${number(report.parameters.minimum_component_cells)} cells`;
    $("public-access").textContent = text(report.public_access_status);
    $("historical-linkage").textContent = text(report.historical_acquisition_linkage);
    drawList();
    $("app").hidden = false;
    await drawMap();
    if (report.components.length) select(report.components[0].component_id);
    else renderEmptyDetails();
    $("header-status").textContent = `Validated ${number(report.components.length)} components · run ${report.run_id}`;
  }

  async function load() {
    try {
      const responses = await Promise.all([
        fetch("/data/candidates.json", { cache: "no-store" }),
        fetch("/data/candidates.geojson", { cache: "no-store" }),
        fetch("/data/candidates-bng.json", { cache: "no-store" }),
      ]);
      if (responses.some((response) => !response.ok)) {
        const messages = await Promise.all(responses.map((response) => response.ok ? "" : response.text()));
        const detail = messages.find((message) => message) || "";
        throw new Error(`Cannot load candidate inputs (HTTP ${responses.map((response) => response.status).join("/")})${detail ? `: ${detail.trim()}` : ""}`);
      }
      const [report, geojson, bng] = await Promise.all(responses.map((response) => response.json()));
      validateJoin(report, geojson, bng);
      await render(report, geojson, bng);
    } catch (error) {
      fail(`Candidate outputs were not displayed: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  $("list-previous").addEventListener("click", () => {
    if (state.listPage > 0) { state.listPage -= 1; drawList(); }
  });
  $("list-next").addEventListener("click", () => {
    if (!state.report) return;
    const pageCount = Math.ceil(state.report.components.length / LIST_PAGE_SIZE);
    if (state.listPage + 1 < pageCount) { state.listPage += 1; drawList(); }
  });

  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    if (!state.report || resizeFrame) return;
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = 0;
      drawMap();
    });
  });

  load();
})();
