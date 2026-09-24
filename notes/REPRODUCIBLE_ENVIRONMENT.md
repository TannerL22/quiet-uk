# Quiet UK reproducible environment

This documents the reviewed installation and verification path for the Quiet UK backend. It is an environment verification record, not a scientific release and not proof of acoustic accuracy.

## Source-only verification

The default suite can run from a clean source checkout. It does not require the
England catalogue, local generated artifacts, live provider services or a running
app. Tests create their own small datasets and loopback HTTP servers. Nine viewer
tests previously depended on `artifacts/candidate_screening_pilot_v2`; they now
use a two-component synthetic catalogue with known geometry and withheld cells.
Real provider responses in `tests/fixtures/provider` exercise both metadata page
formats, WCS 1.0/2.0 descriptions, request identity, grid phase and zero/nodata
encodings. Their README records provenance, licence and scope.

For Windows CPython 3.14, create a fresh environment and install:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-py314-amd64.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q -rs --strict-markers
```

On other supported combinations, install `python -m pip install -e ".[dev]"`
in a fresh virtual environment, then run `python -m pip check` and
`python -m pytest -q -rs --strict-markers`. Dependency installation needs package
access; the tests use local data. The CI matrix covers Windows/Linux with Python
3.12 and 3.14, uses the exact lock only on Windows 3.14, and records resolved and
native geospatial versions elsewhere. A resolver install is not an exact lock.
See `.github/workflows/verify.yml` for the executable checks.

On 24 September 2026, the updated tracked/unignored source was copied into
`artifacts/clean_checkout_verification_v1/source`, without generated data or
environments. A new sibling virtual environment installed all 35 Windows lock
entries and the copied project; `pip check` passed. The full default suite passed
**446 tests, with two Windows-related skips and three release checks deselected**.
The explicit release command failed with clear missing-data errors in that copy,
then passed all three checks with `--release-root=...` pointing to the existing
data checkout. Local JUnit evidence is preserved in the parent verification
directory. Hosted results are available under
[Clean checkout verification](https://github.com/TannerL22/quiet-uk/actions/workflows/verify.yml);
the local result alone does not establish Linux compatibility.

The [first hosted run](https://github.com/TannerL22/quiet-uk/actions/runs/36041725652)
subsequently passed all four jobs on source commit `a25b79c`:

| Fresh hosted checkout | Default suite |
|---|---|
| Linux, Python 3.12 | 448 passed, 3 release checks deselected |
| Linux, Python 3.14 | 448 passed, 3 release checks deselected |
| Windows, Python 3.12 | 447 passed, 1 platform skip, 3 release checks deselected |
| Windows, Python 3.14, exact lock | 447 passed, 1 platform skip, 3 release checks deselected |

Resolved environments emit pending-deprecation warnings about affine matrix
multiplication in Rasterio and `source_pilot`, recorded in the CI logs. These
remain a dependency-maintenance follow-up; compatibility with every future
resolver result is not established.
The hosted Windows runner permits the symlink test skipped on the local machine.
The remaining Windows skip concerns replacing an open SQLite database.

### Opt-in production-release checks

```text
python -m pytest --run-release-checks -m release_data -q
python -m pytest --run-release-checks -m release_data --release-root=PATH_TO_DATA_CHECKOUT -q
```

These retain the actual 128-component join, representative points against the
reviewed England catalogue, and exact rasterised geometry membership. Without
`--run-release-checks`, they are reported as deselected. With it, absent inputs
are errors, not skips; `--release-root` alone does not enable them. Omit
`-m release_data` to run both suites together. They verify the preserved release,
not scientific accuracy or a complete national rebuild.
Use the `--release-root=...` form so pytest does not mistake an external data
directory for a test-discovery root; quote the entire argument if it contains spaces.

The records below describe earlier, data-populated environment checks. Their
historical pass counts are not evidence of clean-checkout portability.

## Historical tested combination

- CPython `3.14.2`
- Windows 11, `AMD64`, 64-bit
- Package version `quiet-uk 0.2.0`
- Exact dependency lock: `requirements-windows-py314-amd64.lock`
- Verification environment: `.venv-repro-check` (ignored by Git)
- Catalogue used for read-only smoke checks: `artifacts/england_catalogue_v3`, build ID `304d0ae422a84bebb83bcb7a7379e236`

The lock is intentionally for CPython 3.14 on Windows AMD64. It is not a universal lock for other Python minor versions, operating systems, or architectures. Package availability and runtime/test offline behavior are separate concerns: the installation needs package access, while the checks below use the preserved local catalogue and rasters without network acquisition.

The original v1 verification record remains preserved. The post-candidate-screening wheel, test, import, and pilot verification is recorded separately in `artifacts/environment_verification_v2/environment_verification.json`.

## Dependency organization

`pyproject.toml` is the authoritative declaration of direct dependencies.

| Role | Distribution | Import(s) | Reason |
|---|---|---|---|
| Runtime | `numpy` | `numpy` | Acoustic arrays, validation, catalogue and reports |
| Runtime | `requests` | `requests` | WCS and mask service requests |
| Runtime | `OWSLib` | `owslib` | WCS discovery |
| Runtime | `rasterio` | `rasterio` | GeoTIFF, windows, transforms and GDAL-backed raster operations |
| Runtime | `matplotlib` | `matplotlib` | Production QA plotting used by `national_qa` |
| Research | `scipy` | `scipy.optimize`, `scipy.special`, `scipy.stats` | Phase 2 road experiments |
| Research | `shapely` | `shapely` | Phase 2 geometry and spatial indexing |
| Research | `pyshp` | `shapefile` | Phase 2 Shapefile input; distribution/import names differ |
| Development | `pytest` | `pytest` | Existing test suite |
| Development | `build`, `setuptools`, `wheel` | build tooling | Reproducible wheel construction |

The `dev` extra includes the research dependencies because the existing full test suite imports the Phase 2 modules. The research dependencies remain separately available as `.[research]`; they are not required by the core catalogue/reporting path. Resolver-installed packages such as `affine`, `lxml`, `PyYAML`, `python-dateutil`, and the other entries in the lock are transitive and are not repeated as direct project dependencies.

`requirements.txt` is retained only as a compatibility installer and contains a local `.` delegation to the package metadata. It does not maintain a second dependency list.

## Exact PowerShell installation and wheel verification

Run from the repository root. The existing `.venv` is not used as an installation target.

```powershell
py -3.14 -m venv .venv-repro-check
$python = (Resolve-Path .\.venv-repro-check\Scripts\python.exe).Path
& $python -m pip install --requirement requirements-windows-py314-amd64.lock
& $python -m build --wheel --no-isolation --outdir dist\repro-check
$wheel = Get-ChildItem -LiteralPath dist\repro-check -Filter *.whl | Select-Object -First 1
& $python -m pip install --no-deps --force-reinstall $wheel.FullName
& $python -m pip check
& $python -m pytest -q -rs -o pythonpath=
```

The `--no-isolation` wheel build is deliberate: the pinned `setuptools`, `wheel`, and `build` versions are already installed from the lock. Installing the wheel with `--no-deps` is safe only after installing the lock; it proves that the package itself is installable without silently resolving a second dependency set.

The compatibility path remains:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For an unconstrained development install, use `python -m pip install -e ".[dev]"`. For the reviewed reproduction, use the lock and the wheel commands above.

## Lock regeneration

The checked lock started from the existing working environment's reviewed `pip freeze` output, with only the declared build tools added. To generate a candidate from a newly reviewed CPython 3.14 AMD64 environment:

```powershell
$python = (Resolve-Path .\.venv-repro-check\Scripts\python.exe).Path
$rawFreeze = @(& $python -m pip freeze)
# The wheel-installed project really appears as quiet-uk==0.2.0 here.
# Inspect it before filtering so the exclusion is visible and reviewable.
$rawFreeze | Where-Object { $_ -match '^quiet[-_]uk(==|\s*@\s*file:)' }
$freeze = $rawFreeze |
    Where-Object {
        $_ -notmatch '^pip==' -and
        $_ -notmatch '^quiet[-_]uk(==|\s*@\s*file:)' -and
        $_ -notmatch '^-e\s+'
    }
if ($freeze | Select-String -Pattern 'quiet[-_]uk|(^|[\\/])quiet-uk-starter|file:' ) {
    throw 'Lock candidate still contains quiet-uk or a local path/editable reference.'
}
[System.IO.File]::WriteAllLines(
    'requirements-windows-py314-amd64.lock.candidate',
    $freeze,
    [System.Text.UTF8Encoding]::new($false)
)
```

Review the candidate against the direct-dependency declarations and the intended platform before replacing the lock. Do not copy unrelated packages or an editable VCS reference into it. The lock deliberately excludes the local `quiet-uk` project and the bootstrap `pip` distribution.

The `build_identity.py` compatibility change makes the implementation fingerprint resolve correctly from an installed wheel as well as from a source checkout. Because `build_identity.py` is itself part of the implementation fingerprint, changing that file changes the build identity. Older resumable builds or manifests can therefore fail compatibility checks after this change. That failure is intentional: do not bypass it or silently resume with an older identity; rebuild from the reviewed inputs.

## Verification commands and outcomes

The verification environment installed the exact lock, built `dist/repro-check/quiet_uk-0.2.0-py3-none-any.whl`, uninstalled the editable project, and installed that wheel. The wheel SHA-256 was `c6b5bd8fef58c9431081dc1e42f967f7c68c3e27eb9bc5a9536901f51d1f4b7b`.

| Check | Command or method | Outcome |
|---|---|---|
| Lock consistency | Compare installed distributions with the lock after canonicalizing names | 35 lock entries matched; only bootstrap `pip` was intentionally outside the lock |
| Dependency health | `.venv-repro-check\Scripts\python.exe -m pip check` | Passed: no broken requirements |
| Outside-checkout import | Run from `%TEMP%`; import all core, catalogue/reporting, and research modules | Passed; `quiet_uk` resolved from installed `site-packages`, with no checkout or `src` path entry |
| CLI help | `--help` for scripts 09, 10, 13, 14, 15, 17, 19, 20, and 21 | All passed |
| Full suite | `python -m pytest -q -rs -o pythonpath=` | `269 passed, 2 skipped in 33.77s` |
| Wheel proof | Build wheel, uninstall editable package, install wheel with `--no-deps` | Passed |
| Real catalogue smoke checks | Five offline reads through installed `quiet_uk.catalogue` | All expected states reproduced; see below |

The expected skips are platform-specific: Windows may prohibit replacing an open SQLite database, and symbolic links are unavailable in one runner test in this Windows environment.

## Native geospatial runtime

The tested environment reported:

- Rasterio `1.5.1`
- GDAL `3.12.4` (`rasterio.__gdal_version__`)
- PROJ `9.8.1` (`rasterio.__proj_version__`)

These native versions come from the tested Rasterio wheel/runtime. They are not independently portable across operating systems and should be recorded again when testing another platform.

## Offline catalogue smoke checks

Using the preserved v3 catalogue, authoritative England mask, and existing tile directory, the installed wheel returned:

| BNG coordinate | Expected check | Observed result |
|---|---|---|
| `414550 567550` | All bands qualified | Tile `r0009c0033`; England land; all four bands `qualified` |
| `341650 576450` | Airport-dependent bands withheld | Tile `r0008c0025`; airport-dependent bands withheld; road/rail upper qualified |
| `391150 649650` | Airport coverage limitation | Tile `r0000c0030`; airport limitation; zero airport fraction labelled not silence |
| `382650 657550` | Outside England land | Tile `r0000c0030`; all bands unavailable with `outside_england_land` |
| `82599 331450` | Outside dataset coverage | No owning tile; all bands unavailable with `no_dataset_coverage` |

No network calls, source downloads, raster acquisition, catalogue rebuild, or national processing job was used for these checks.

## Limitations

- The lock is exact only for the tested CPython 3.14 Windows AMD64 environment; other platforms require a separately reviewed lock.
- The lock pins package versions but does not vendor wheels or guarantee future package-index availability.
- Rasterio's GDAL/PROJ runtime is supplied by its platform wheel and may differ on another platform.
- The package metadata keeps compatible version ranges for ordinary installs; exact reproduction requires the platform lock.
- Data, manifests, catalogue files, source rasters, provider services, and credentials are not packaged or included in the environment lock.
- The verification proves installation/import/test/smoke behavior for the reviewed baseline. It does not prove source provenance, scientific accuracy, or acoustic model validity.
