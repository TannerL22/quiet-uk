from quiet_uk import wcs


class _FakeResponse:
    content = b"II*\x00fake-tiff"
    headers = {"content-type": "image/tiff"}

    def raise_for_status(self):
        return None


def test_wcs20_request_uses_padded_subsets_and_image_tiff(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(wcs.requests, "get", fake_get)
    data = wcs.get_coverage_wcs20(
        "https://example.test/wcs",
        "airport__Airport_Noise_ALL_Lden",
        (503000, 171000, 513000, 181000),
        1000,
        1000,
    )

    assert data.startswith(b"II*")
    params = captured["params"]
    assert ("coverageId", "airport__Airport_Noise_ALL_Lden") in params
    assert ("format", "image/tiff") in params
    assert ("outputCRS", "http://www.opengis.net/def/crs/EPSG/0/27700") in params
    assert ("subset", "E(502995.0,513005.0)") in params
    assert ("subset", "N(170995.0,181005.0)") in params
    assert ("scaleSize", "i(1001),j(1001)") in params


def test_wcs20_inventory_request_can_disable_padding(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr(wcs.requests, "get", fake_get)
    wcs.get_coverage_wcs20(
        "https://example.test/wcs", "airport__x",
        (500000, 170000, 501000, 171000), 100, 100,
        padding_cells=0,
    )
    params = captured["params"]
    assert ("subset", "E(500000.0,501000.0)") in params
    assert ("subset", "N(170000.0,171000.0)") in params
    assert ("scaleSize", "i(100),j(100)") in params
