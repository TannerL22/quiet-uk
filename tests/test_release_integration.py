"""Preserved production-release assertions, excluded from source-only tests."""
import pytest

from quiet_uk.catalogue import DatasetCatalogue
from test_candidate_viewer import (
    assert_representative_points,
    assert_geometry_membership,
    viewer_server,
)

pytestmark = pytest.mark.release_data


@pytest.fixture(scope="module")
def released_candidates(release_root):
    root = release_root / "artifacts/candidate_screening_pilot_v2"
    return viewer_server.read_candidate_payloads(root / "candidates.json", root / "candidates.geojson")


def test_reviewed_128_component_join(released_candidates):
    report, geojson = released_candidates
    viewer_server.validate_candidate_payloads(report, geojson)
    assert len(report["components"]) == len(geojson["features"]) == 128


def test_representative_points_against_reviewed_catalogue(release_root, released_candidates):
    with DatasetCatalogue(
        release_root / "artifacts/england_catalogue_v3",
        release_root / "data/processed/england/tiles",
        release_root / "data/processed/england_mask/england_100m_mask.tif",
    ) as catalogue:
        assert_representative_points(released_candidates[0], catalogue)


def test_reviewed_geometry_retains_grid_membership(released_candidates):
    assert_geometry_membership(released_candidates)
