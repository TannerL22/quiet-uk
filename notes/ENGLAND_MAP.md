# England map at native 10 m — 3 November 2026

Quiet UK's 10 m explorer is growing from the 5,000 km² Oxford–London footprint to
all of England, using Defra's Round 4 road, rail and aircraft noise grids. This
note covers the download, the map built from it, how its accuracy is protected, and
what is still running.

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
  fall between pixel centres. No averaging or smoothing is applied.
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
- **Colours.** These are the existing Quiet UK bands (8 colours, 40–70+ dB), a pale
  teal for under the cutoff, and a light hatch for no data.

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
- **Location:** PROJ's user folder, not the repository. On another machine, install it with
  `python -m pyproj sync --file uk_os_OSTN15_NTv2_OSGBtoETRS`.

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

## Use

```text
python scripts/124_build_england_map.py --preview --output artifacts/england_map_preview
python scripts/124_build_england_map.py              # after the download is sealed
python scripts/124_build_england_map.py --verify
```

The launcher opens the England map by default. It uses `england_map_v1` when present,
otherwise the preview. Oxford–London tools such as saved comparisons and the
evidence ZIP stay at `/pilot`.

Measured on this laptop:

- **Preview** (90 squares): 33 blocks, about 3 minutes with 6 workers, 106 MB.
  With OSTN15 (218 squares, built while the download ran): 7 minutes, 202 MB,
  `--verify` 1.5 minutes.
- **Full map** (projected): about 350 blocks, 30–40 minutes, about 1.5 GB.
- **Clicks:** about 0.4 s for the first click in a square, 1 ms after that.

## Not yet done

- Finish the download, then seal it, build `england_map_v1` and verify it. A Claude
  app update stopped the first run on 3 November at 1,801 of 13,455 rasters. It
  resumed on 4 November, launched independently of the app, with about 14 hours left.
- Back up `artifacts/` off this machine (e.g. Google Cloud Storage).
- Scotland, Wales and Northern Ireland need their own providers and adapters.
- Values below the cutoffs do not exist at Defra. Quieter places can only be bounded
  until we calculate or measure them.
