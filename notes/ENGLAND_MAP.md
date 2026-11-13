# England map at native 10 m — 3 November 2026

Quiet UK's 10 m explorer has grown from the 5,000 km² Oxford–London footprint to
all of England, using Defra's Round 4 road, rail and aircraft noise grids. The
download sealed and the full map was built and verified on 5 November 2026. This
note covers the download, the map built from it, how its accuracy is protected, the
interface, and what is not done yet.

## In one paragraph

`scripts/123_acquire_england.py` downloads every 10 km square of England that
touches land: 1,495 squares, each with 9 layers (3 sources × Lden, day, night).
The 50 Oxford–London squares are reused byte for byte. `scripts/124_build_england_map.py`
turns the download into a zoomable map without copying or changing the original
rasters. Clicking any point reads the original Defra cell and shows its value with
the evidence chain behind it.

## Download (`src/quiet_uk/england_acquisition.py`)

- **Plan.** 10 km squares on the Oxford–London lattice (cell edges at …5 m), with a
  one-cell halo, ordered south to north. The plan is fixed by the England mask's
  checksum and the Oxford–London plan.
- **Pinned requests.** New squares use exactly the request recipes Oxford–London
  used. Before downloading, a probe re-fetches one Oxford–London raster per layer
  and must match it. All nine were **byte-identical** on 3 November 2026, so Defra
  is still serving the same data. The live catalogue must still list the same
  datasets, periods, grids and envelopes. Only the WCS protocol prefix of a
  coverage name may differ.
- **Storage.** Each response is stored with lossless zstd compression (about 7% of
  its original size). The compressed copy is decompressed and compared with the
  response before it replaces anything. Every record keeps the SHA-256 of the exact
  provider bytes, the request journal and quality checks.
- **Resume and stop.** The download resumes from saved records. Create an empty file
  named `STOP` in `artifacts/england_acquisition_v1` to stop after the current
  raster. Temporary server errors are retried after 1, 5 and 15 minutes. A layer
  that still fails is set aside and retried on the next run. Budget, disk and
  identity checks stop the run outright.
- **Seal.** When every square is done, `--verify` replays every record from local
  evidence and compares every seam between neighbouring squares exactly. Sealing
  writes `records.json` and a manifest with a release ID.

Started 3 November 2026: 13,455 rasters (450 reused). About a fifth of them
are aircraft layers outside any airport grid, which need no download. Archives so
far average 0.38 MB per raster, so England should take about 4 GB on disk after
roughly 40–50 GB of transfer. Progress is logged to
`artifacts/england_acquisition_v1.log`.

## Map (`src/quiet_uk/england_map.py`)

- **Tiles.** Web Mercator PNG tiles for zoom 5–14, stored in one SQLite file.
  At every zoom, **each pixel shows the native 10 m cell under its centre**. That
  cell is found by converting the pixel centre to British National Grid and picking
  the owning 10 km square (west and north edges included). At zoom 14 a pixel is
  5.4–6.1 m on the ground, so every cell is drawn. Zoomed out, thin features can
  fall between pixel centres. No averaging or smoothing is applied. Since 6 November
  the page draws zoomed-out views from a separate **overview** of area averages
  instead; see [Overview](#overview-srcquiet_ukengland_overviewpy).
- **Close-up cells.** From zoom 15.5 the page swaps the tiles for 1 km images drawn
  straight from the original grid, one image pixel per 10 m cell. Cell edges are
  then exact, rather than shifted by up to one zoom-14 pixel (about 6 m).
- **Point values.** A click converts the point to British National Grid, finds its
  owning square and reads the cell from the archived provider response. The SHA-256
  is checked on every read. Values are given to Defra's 2 decimal places. The
  response includes the record, the request journal, the cell outline and the
  evidence interpretation.
- **Evidence contract v2.** Following [Defra's reply](DEFRA_DATA_REQUEST_2026-10-03.md#provider-reply),
  a road or rail **zero** is a calculated level strictly below the inclusive cutoff.
  It is shown as **under 40 dB** (Lden, day) or **under 35 dB** (night): an upper
  bound, never a value. `-96` and TIFF nodata stay **no data**. Aircraft is mapped
  around airports only; outside its grids the map is blank, which means not mapped,
  not quiet.
- **Colours.** Tiles are stored with the palette they were built and verified with:
  the earlier Quiet UK bands (8 colours, 40–70+ dB), a pale teal for under the
  cutoff, and a light hatch for no data. That palette stays in the manifest. The
  page shows a different palette; see [Interface](#interface).

### Integrity

The map manifest pins:

- the acquisition state and plan checksum;
- the SHA-256 of its record list;
- a digest of the tile index (every tile's position and SHA-256);
- the exact coordinate transformation, with check points and the SHA-256 of its grid file.

At startup the server re-reads the whole index and requires the digest to match.
It also requires the same grid file and the transformation to reproduce the check
points. Each tile's
bytes are hashed again whenever the tile is served. `--verify` does three things:

- hashes every tile;
- re-hashes every archived response;
- redraws three sample blocks and requires byte-identical tiles.

Tests (`tests/test_england_map.py`) sample pixels at zooms 14, 13, 11 and 8. Each
must show exactly the cell a click reads. The tests also check halo cells, tile
ownership at a boundary, the close-up images, and tampering with tiles, the index,
archives, records, the manifest and the transformation grid.

### Coordinate accuracy

Map positions use PROJ's best installed WGS 84 → British National Grid operation.
Since 4 November 2026 that is Ordnance Survey's **OSTN15** grid. OSTN15 is Ordnance
Survey's definitive transformation from ETRS89 to the National Grid.

- **File:** `uk_os_OSTN15_NTv2_OSGBtoETRS.tif`, 3,035,814 bytes, SHA-256
  `5d6ed64d2119952c4c559fa1fccbc594b6520fc3ec3ef2fc10be13202c4384fa`.
- **Source:** the PROJ CDN, via `pyproj sync`. The file matches the checksum the CDN
  publishes.
- **Licence:** 2-Clause BSD, derived from work by Ordnance Survey.
- **Location:** `artifacts/proj/` inside the project (not committed). The map also
  looks in pyproj's own data folder and PROJ's user folder. On another machine, install
  it with
  `python -m pyproj sync --file uk_os_OSTN15_NTv2_OSGBtoETRS --target-directory artifacts/proj`.
- **Why not only PROJ's user folder:** on Windows, files that a packaged app (such as
  the Claude desktop app) writes to `%LOCALAPPDATA%` go to that app's private copy,
  `%LOCALAPPDATA%\Packages\<app>\LocalCache\Local`. Programs started from File
  Explorer cannot see it. The grid was first installed that way on 4 November 2026,
  so on 6 November the launcher failed with "grid … is missing". The project folder
  is the same for every program.

The manifest records the grid's name, URL, size and SHA-256. The server refuses to
start if the file is missing or different, and the check points must still reproduce
within 1 µm.

EPSG states 1 m for this route. That figure is mostly the gap between WGS 84 and
ETRS89, the frame OSTN15 starts from; PROJ treats the two as the same.

Before OSTN15, the map used the Helmert transformation *OSGB36 to WGS 84 (6)*
(stated 2 m). At the centres of all 1,495 squares it placed positions:

- 1.78 m from OSTN15 (median);
- within 3.08 m for 95% of squares;
- 4.81 m away at worst (Isles of Scilly).

Noise values were never affected. Only two things were: where cells are drawn on the
basemap, and which cell a click near a cell edge lands in.

Installing the grid also moved `pyproj`'s choice for the older Oxford–London display
images but not GDAL's, which opened seams between their tiles. Those displays now
pin the operation they were built with; see
[the tiled map note](TILED_MAP.md#pinned-coordinate-operation-5-november-2026).

## Overview (`src/quiet_uk/england_overview.py`)

Zoomed out, one screen pixel covers hundreds of 10 m cells. Showing the one cell
under each pixel centre (the map's own tiles) makes motorways flicker in and out
and turns the country view into noise. The overview is a separate display product,
built from the same archived rasters through a verified map release, that summarises
instead of sampling:

- **Averaged tiles, zooms 5–13.** Each pixel is the energy average
  `10·log10(mean(10^(L/10)))` of the native cells under it. Cells are sampled at the
  zoom-14 pixel centres, so each counts in proportion to its area. Cells under the
  cutoff count as silence, so an average is a **lower bound** (in the design check on real
  England data the median gap to the upper bound, which counts them at the cutoff, was
  0.03 dB). An average under the cutoff is drawn as under it; no-data cells are left
  out; a pixel with no calculated cells is no data. The tiles use the map's palette
  indices, so the same looks apply. From zoom 14 the page uses the map's own tiles,
  and from 15.5 the exact cell images, as before.
- **Quiet-area hexagons.** A pointy-top hexagon grid on the British National Grid,
  3 km radius (about 5.2 km across). Every native cell is assigned by its centre and
  counted by status: reported value, under the cutoff, no data. Each hexagon also
  stores the energy sum, the count at 55 dB or more, and the count at or over the
  WHO 2018 guideline for its layer. `/api/england/hexes` turns these into shares.
- **A display aid, not analysis.** The manifest says so (`analytical_use: false`).
  Clicking still reads the original cell at every zoom.
- **Integrity.** The overview manifest pins the map release and its manifest hash,
  the tile index digest, the hexagon file hash and the code that built it. The server
  checks all of these at startup and each tile's hash when it is served. If a check
  fails, the server says so and runs without the overview (zoomed-out tiles go back
  to samples); the exact map is unaffected. `--verify` hashes every tile, redraws
  three blocks byte for byte and recounts the hexagons.
- **Tests** (`tests/test_england_overview.py`) compare averaged pixels at zooms 13, 12,
  11 and 9 with independent energy averages of point lookups, count every native cell
  of two squares into hexagons by point-in-polygon, and check tampering, the server
  routes and the order of every colour ramp in OKLab lightness.

## Where England ends (`src/quiet_uk/england_outline.py`)

Defra's squares along the Welsh and Scottish borders, and along the coast, hold "no
data" for the part outside England. Drawn as stored, those parts made a staircase of
grey hatched 10 km squares. The page now leaves them out, using the ONS country
boundary. This is a drawing step only.

- **Boundary.** ONS *Countries (December 2025) Boundaries UK BGC*: generalised to
  20 m, clipped to the coastline, in British National Grid. Open Government Licence
  v3.0 (Source: Office for National Statistics; contains OS data © Crown copyright and
  database right 2025). Downloaded on 6 November 2026 with the owner's approval from
  the ONS Open Geography Portal (item `c8bea35f51054f0c95aa876f66fe027c`) to
  `artifacts/boundaries/ons_countries_2025_bgc/Countries_December_2025_Boundaries_UK_BGC.geojson`:
  8,996,850 bytes, SHA-256 `8636470792773d500b467f442392320a987dcfe34d1b6e9b10124b135217c837`.
  The server checks the size and hash at startup; if they differ, it runs without
  the boundary and draws tiles exactly as stored.
- **What changes.** In tiles drawn for the page (`/england-display/`) and the close-up
  cell images, a *no-data* pixel whose centre is outside England becomes transparent.
  Nothing else changes: values and under-cutoff cells are drawn wherever they are (Defra
  calculated some cells beyond the line, mostly in estuaries and a strip along the
  border, and those stay); inside England every pixel is as stored, no-data included.
  Stored tiles, `/england-tiles/`, clicks, downloads and comparisons are untouched.
- **How a pixel is placed.** Its centre goes to the National Grid with the map's own
  pinned OSTN15 transformation and is tested against the boundary there. To keep it
  quick, the transformation is exact on a 17 × 17 lattice per tile and bilinear
  between; a pixel next to a lattice point beyond the grid's extent is transformed
  exactly. Checked on real tiles: the position is within 2% of a pixel at every zoom
  (53 m on 3 km pixels at zoom 5, under 3 cm from zoom 11), and only a handful of
  pixels right on the line differ from an exact test (13 of 197,000 at zoom 5, none
  from zoom 11). A tile is first tested by its outline, so only tiles that cross the
  line are tested pixel by pixel: 10–80 ms each, once, then cached for every layer
  and look.
- **On the page.** A dashed line marks England's borders with Wales and Scotland (the
  edges the ONS polygons share, simplified to 10 m). Below zoom 11.5 a light wash covers
  everything outside England (England simplified to 150 m; islands under 2 km² left
  out); it is cut into half-degree pieces because MapLibre's triangulation of one large
  shape with an England-shaped hole drew stray triangles across England. Hovering
  there says "Outside England"; a click in Wales or Scotland says so in the card, and
  the location response gains a `country` field (context only; the evidence is
  unchanged). Tile and outline addresses carry the boundary's hash, so a browser never
  mixes tiles from before and after a change.
- **Tests** (`tests/test_england_outline.py`) use a stand-in boundary through a no-data
  block of the test map. They check the file pin, every mask pixel against an exact
  test, that only no-data outside England changes, the cell images, the server routes
  (raw tiles unmasked, stale boundary refused, `country`), running without a changed
  file, and the fonts.

## Search (`src/quiet_uk/place_names.py`)

Added 7 November 2026. Suggestions appear as you type, from a copy of Ordnance
Survey's **OS Open Names** kept on this computer, so nothing typed leaves it.

- **Source.** OS Open Names, October 2026 release, CSV (`opname_csv_gb.zip`,
  103,312,081 bytes, SHA-256 `81ee5ba2…0a74532`), downloaded on 7 November 2026
  with the owner's approval and checked against Ordnance Survey's published size and
  MD5. Open Government Licence v3: *Contains OS data © Crown copyright and database
  right 2026*, shown in the page's credits. It sits in
  `artifacts/place_names/os_open_names_2026_10/` and is not in Git.
- **Index.** `scripts/126_build_place_names.py` checks the download against its pin
  (size and SHA-256) and its column header, converts every point from the National
  Grid with the England map's own pinned transformation, and writes
  `artifacts/place_names_v1/` (a SQLite file of 600 MB and a manifest with its hash,
  counts, the source pin and hashes of the code that built it). 3,050,396 names:
  43,274 settlements, 996,656 roads, 266,532 other features, 1,743,934 postcodes and
  2,863 postcode districts (the mean of their postcodes). Release
  `place-names-c50e4a108e35cf6474f9`; about 10 minutes to build. The server checks the
  file's hash in the background at startup (about 2 s) and opens it read-only.
- **Matching.** Names are matched word by word from the start of each word, ignoring
  case, accents and apostrophes, with common abbreviations (rd, st, ave …). Words that
  match only the town or county count, at a cost. Leading house numbers are dropped,
  since OS Open Names has streets, not addresses. A postcode or district goes first.
  Then whole-name matches beat partial ones, cities and towns beat villages, then
  hamlets, roads and other features; once the map is zoomed in on an area, closer
  places win ties. At most three of the same name are shown. Measured on the full
  index: 1–85 ms per keystroke.
- **What a choice does.** A town, village, area or district moves the map there
  without picking a spot. A road, postcode or feature also checks the spot at its
  point, with a note that a road's point is on the road itself (louder than the homes
  beside it) and a postcode's is at one address in it. Names never feed a noise
  value: the value always comes from the 10 m cell.
- **Names for clicked spots.** A clicked spot's card is named after the nearest
  postcode within 400 m ("Near OX1 3HY, Oxford"), else the nearest settlement within
  3 km, else its position. Under 5 ms. Context only.
- **Anything else** (a shop, a pub) is one choice away: the last suggestion sends that
  search to OpenStreetMap Nominatim, as Enter did before. Nominatim's policy rules out
  sending it every keystroke. Enter takes the highlighted or best local match, and
  goes to Nominatim only when there is none. Without the index, the page falls back to
  search on Enter.

## Area summaries (`src/quiet_uk/england_area.py`)

Added 7 November 2026. **Area** (top right) lets you drag a box, or use the current
view, up to 100 km² and 20 km across, and summarises it for the road or rail layer
on show. Aircraft is not offered: it has no zero rule and is mapped only around
airports. When Defra publishes its Combined Transport Noise product it can be
summarised the same way; we do not combine sources ourselves.

- **Cells.** Every native 10 m cell whose centre is inside the box, read from the
  same verified archives as a click and from the Defra square that owns it. The box's
  edges are converted to the National Grid with the map's pinned transformation.
  Nothing is resampled and no display tile is used.
- **Shares** are of calculated cells (a value, or under the cutoff). Cells with no
  published value are counted separately and never treated as quiet.
- **Typical square** (median): the level half the calculated cells are at or under;
  "under the cutoff" when at least half are.
- **Energy average** 10·log10(mean 10^(L/10)), given as a range because cells under
  the cutoff could be anywhere from silence to the cutoff. It is dominated by the
  loudest cells, which is why the median is shown first.
- **Loudest square**, with ties counted.
- **Quietest spot**: among cells under the cutoff, the one furthest from any cell at
  or over a chosen level (default: the WHO guideline for the layer; 55 dB in the
  daytime, which has none), or with no published value, which could hide noise.
  Distances are between cell centres, by an exact Euclidean distance transform
  (SciPy). Cells up to 2 km beyond the box count; a distance that would reach past
  that is reported as "at least". If no cell is under the cutoff, the lowest
  published level stands in. The page draws a dotted line to what limits it.
- **Evidence.** The JSON download holds every count, both places with their cells,
  National Grid positions and record hashes, the records read, the zero rule and the
  method; the CSV spells out the same figures. Shared view links carry the box
  (`#area=`) and it is summarised again from the current release.
- **Speed.** About 0.5–1.1 s for 86 km² over Oxford, once its squares are loaded.
- **Tests** (`tests/test_england_area.py`) compare every count, band, the median, the
  average, the loudest cell and the quietest spot's distance against answers worked
  out cell by cell from the test rasters' definitions with an independent
  nearest-neighbour search, and tie that grid to the map with point lookups.

## Use

```text
python scripts/124_build_england_map.py --preview --output artifacts/england_map_preview
python scripts/124_build_england_map.py              # after the download is sealed
python scripts/124_build_england_map.py --verify
python scripts/125_build_england_overview.py         # averaged zoom levels and hexagons
python scripts/125_build_england_overview.py --verify
python -I scripts/126_build_place_names.py           # search index from the OS Open Names download
```

The launcher opens the England map by default. It uses `england_map_v1` when present,
otherwise the preview. The Oxford–London explorer, with its regional evidence ZIP
and original comparison workflow, stays at `/pilot`.

Measured on this laptop:

- **Preview** (90 squares): 33 blocks, about 3 minutes with 6 workers, 106 MB.
  With OSTN15 (218 squares, built while the download ran): 7 minutes, 202 MB,
  `--verify` 1.5 minutes.
- **Full map** (5 November 2026): 328 blocks, 750,480 tiles, 1.4 GB, 80 minutes;
  `--verify` 32 minutes. Release `england-map-69928c8a2ecdb96ac33c`, built from the
  sealed download `england-0f4eb6e6cb6e2dd82687`.
- **Overview** (6 November 2026): 191,505 averaged tiles and 6,048 hexagons, 493 MB,
  57 minutes; `--verify` 9 minutes. Release `england-overview-0d48222be0df113cca2b`.
  A separate spot check of 35 random pixels from the finished tiles (zooms 12, 11
  and 9; road, rail and aircraft) against independent energy averages of point
  lookups matched every one.
- **Clicks:** about 0.4 s for the first click in a square, 1 ms after that.
- **Boundary:** loads in about 1 s at startup; the page's border and wash (460 KB)
  are made in the background in about 3 s.

## Interface

Rebuilt on 5 November 2026 so that someone with no background in acoustics can use
it, and again on 6 November as a **map-first** page with three looks to choose from.
Nothing the research side relies on was removed: the specialist detail is in the
**Data** drawer, and the table below says where each item is. Files:
`explorer/england.html`, `england.js`, `england-places.js`, `england.css`, and
`src/quiet_uk/map_presentation.py`. Interface version 9 (7 November 2026: suggestions
as you type and area summaries).

- **Layout.** The map fills the window. Search sits top left, with the selected spot's
  card under it. The look switcher and the Places, Data and Help buttons sit top right.
  The legend, bottom left, holds the source and time controls, the colour scale with
  the WHO guideline marked, and the switches. **All sources and evidence**, saved
  places and everything for researchers open in a drawer on the right.
- **Zoom ladder.** Country and region (zoom 5–12): area averages from the overview,
  blended when enlarged. Streets (12–15.5): every 10 m cell, crisp. Close in: the
  exact cell images. The legend says which one is showing.
- **Drawing order.** The noise sits over the background map's roads (so a loud road
  is not hidden under a white line) and under its boundaries and labels. Below zoom
  12 the sea from OpenStreetMap is painted over the colours, so the 10 km squares
  that reach into the sea do not make a blocky coast; it fades out by zoom 12 and
  nothing is hidden closer in. From zoom 14.5 building outlines are drawn over the
  colours for orientation. England's borders with Wales and Scotland are dashed, and
  zoomed out a light wash covers everything outside England (see
  [Where England ends](#where-england-ends-srcquiet_ukengland_outlinepy)).
- **Looks** (`display-v3-looks`). Only colours, fonts and the background map change;
  every look shows the same classes, and the help says so. On 8 November 2026 the
  owner chose to keep all three, with the switcher, for now.
  - **Night glow:** a dark background map and a ramp from deep purple to pale gold
    that gets lighter as it gets louder. Zoomed out, a soft copy of the same tiles,
    drawn four times larger from two zooms out, sits under them as a halo.
  - **Editorial atlas:** warm paper, an ink ramp from sand to oxblood, set in
    Newsreader (a serif with optical sizes, so small labels stay sturdy).
  - **Calm guide:** split at the WHO 2018 guideline for the layer: pale to deeper
    teal for bands wholly under it, sand for the band that contains it, amber to red
    over it. The WHO gives no daytime-only value, so daytime layers use one warm ramp.
  - The choice is kept in the browser and in view links (`#look=`).
  - **Typefaces:** Geist (Night glow), Newsreader (Editorial atlas) and Figtree (Calm
    guide), all SIL Open Font License 1.1, in `explorer/fonts` with their licences.
    Downloaded on 6 November 2026 with the owner's approval from the Fontsource 5.3.0
    packages on jsDelivr (Latin and Latin Extended variable WOFF2, about 301 KB in all);
    each file matched the package's published size and SHA-256. The server sends only
    these named files, and the browser fetches only the look in use. Nothing is loaded
    from elsewhere. There are no italics: the page turns off faked italics and bolds.
- **Pointer readout.** Hovering shows the band under the pointer ("45–50 dB, Fairly
  quiet; area average"). It reads the same tile served with a hidden palette whose
  entry *i* is red *i*·20 (`?theme=index`), so the class is exact for that tile pixel.
  Clicking still reads the original cell.
- **Quiet areas.** A switch in the legend replaces the noise colours with the overview
  hexagons, coloured by the share of each one's calculated land that is under the
  cutoff (five steps of 20%, a green family so it never reads as a noise level).
  Hovering gives the share under the cutoff and the share at or over the WHO
  guideline; with a spot selected, its card adds the same for the hexagon around it.
  Not offered for aircraft, which is only mapped around airports.

- **Search** suggests places as you type from the local OS Open Names index (see
  [Search](#search-srcquiet_ukplace_namespy)); arrow keys and Enter choose one. The
  last suggestion sends the search to Nominatim, whose results keep the "Not this
  one?" list. Typed coordinates are read in the page and never sent anywhere.
- **Area** draws a box (or takes the view) and summarises it in the drawer's **This
  area** tab: a bar and table of the colour bands, the typical square, the share very
  quiet and over the WHO guideline, the energy average, and the quietest and loudest
  spots, marked on the map; see [Area summaries](#area-summaries-srcquiet_ukengland_areapy).
- **Clicking a spot** shows the level, a plain word for its band, and where it sits
  against the WHO's 2018 guideline for that source (road 53 dB Lden / 45 dB night,
  rail 54 / 44, aircraft 45 / 40). **Details** lists every source and time for that
  spot.
- **Band words** (`map_presentation.WORDS`): Very quiet (under 40), Quiet, Fairly
  quiet, Moderate, Noticeable, Loud, Very loud, Extremely loud (70+). At night they
  apply 10 dB lower, matching the night weighting inside Lden. The words describe the
  level only; the health comparison comes from the WHO values.
- **Recolouring.** Tiles are recoloured when served (`?theme=night|atlas|guide`) by
  swapping only the PNG's palette chunks. The pixel data, and so every cell's class,
  is byte-identical to the stored, hash-checked tile. Tests check this for every look
  and layer. The earlier violet ramps (`light`, `dark`) are still served for old links.
- **Saved places:** up to three, compared side by side with the differences in plain
  words. Levels are always re-read from the release (`/api/england/comparison`).
  The CSV and JSON downloads keep full precision, file hashes, cell outlines and
  cutoffs.
- **Phone:** search on top, looks and buttons under it, a compact legend at the
  bottom (**Change** opens the controls). A selected spot's card replaces the legend
  and the map moves the spot above it. The drawer fills the screen.

| Research item | Where it is now |
|---|---|
| What Lden, Lday and Lnight mean | Data → For researchers → Indicators (and a tooltip on the time control) |
| Reference periods, provider records, grid, release, overview release, evidence contract, licence, missing-value policy, uncertainty | Data → For researchers → Data and coverage |
| How the map is drawn (area averages and their method, nearest-cell tiles, exact cells from zoom 15.5, hexagon counts, sea mask, OSTN15, the looks, the pointer readout) | Data → For researchers → How the map is drawn |
| Every source and time at a spot | Spot card → All sources and evidence |
| Selected spot: National Grid position, Defra square, status, stored value, native row and column, record SHA-256, archive, transformation; JSON download | Spot card → All sources and evidence → Selected spot evidence |
| Check the spot at the map centre; copy view link | Data → For researchers → Tools (Enter on the focused map also checks the centre) |
| Recorded sound samples (London) | Data → For researchers → Tools, when that release is installed; a site's details open in the spot card |
| Saved places and comparison | Places |
| Stored palette | `/api/england` → `display.palette_rgba`, unchanged; the page palettes are under `presentation` |
| Overview method, release and hexagon definitions | `/api/england` → `overview`; hexagon shares at `/api/england/hexes` |
| Boundary source, hash and use; the page's border and wash | `/api/england` → `outline`; shapes at `/api/england/outline`; method under Data → How the map is drawn |
| Which country a clicked spot is in | Spot card → All sources and evidence → Selected spot evidence; `country` in the location JSON |
| Oxford–London explorer, regional evidence bundles, original comparison workflow | `/pilot`, linked from Data → For researchers |
| Area summary method, limits and every figure with its evidence | Data → For researchers → Area summaries; the area's JSON and CSV downloads; `/api/england` → `area` |
| Place-name source, version, licence, counts and index release | Data → For researchers → Data and coverage; `/api/england` → `places`; `artifacts/place_names_v1/manifest.json` |

## Not yet done

- Back up `artifacts/` off this machine (e.g. Google Cloud Storage), now including
  `place_names/` (the download) and `place_names_v1/`.
- When Defra's Combined Transport Noise product appears, review it, add it as a layer
  and let the area tool summarise it.
- Scotland, Wales and Northern Ireland need their own providers and adapters.
- Values below the cutoffs do not exist at Defra. Quieter places can only be bounded
  until we calculate or measure them.
