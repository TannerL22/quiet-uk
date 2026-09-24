"""Default tests are source-only; production data checks are explicitly opt-in."""
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-release-checks", action="store_true",
                     help="Include checks requiring preserved local release data.")
    parser.addoption("--release-root", type=Path,
                     default=Path(__file__).resolve().parents[1],
                     help="Repository/data root for opt-in release checks.")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-release-checks"):
        return
    selected, deselected = [], []
    for item in items:
        (deselected if item.get_closest_marker("release_data") else selected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


@pytest.fixture(scope="session")
def release_root(pytestconfig):
    root = pytestconfig.getoption("--release-root").resolve()
    required = (
        "artifacts/candidate_screening_pilot_v2/candidates.json",
        "artifacts/candidate_screening_pilot_v2/candidates.geojson",
        "artifacts/england_catalogue_v3",
        "data/processed/england/tiles",
        "data/processed/england_mask/england_100m_mask.tif",
    )
    missing = [str(root / path) for path in required if not (root / path).exists()]
    if missing:
        pytest.fail("Release checks were requested but required data is missing. "
                    "Use --release-root with a data-populated checkout. Missing:\n"
                    + "\n".join(missing), pytrace=False)
    return root
