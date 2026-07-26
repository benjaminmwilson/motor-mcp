"""Tests for the Galaxy Motors (galmo) datasource."""

from urllib.parse import unquote

from datasources.galmo import build_api_url, PROXY_URL


def _inner_url(full_url: str) -> str:
    """Extract and decode the VMS API URL from the proxy wrapper."""
    after_endpoint = full_url.split("endpoint=", 1)[1]
    encoded = after_endpoint.split("&action=", 1)[0]
    return unquote(encoded)


def test_build_api_url_all_filters():
    url = build_api_url("Toyota", "Camry", 2018, 2022)
    inner = _inner_url(url)
    assert "mk=Toyota" in inner
    assert "md=Camry" in inner
    assert "yr=2018,2022" in inner
    assert "pg=1" in inner
    assert "rpp=25" in inner


def test_build_api_url_make_only():
    url = build_api_url("Honda", None, None, None)
    inner = _inner_url(url)
    assert "mk=Honda" in inner
    assert "md=" not in inner
    assert "yr=" not in inner


def test_build_api_url_year_min_only():
    url = build_api_url(None, None, 2020, None)
    inner = _inner_url(url)
    assert "yr=2020,2020" in inner


def test_build_api_url_year_max_only():
    url = build_api_url(None, None, None, 2019)
    inner = _inner_url(url)
    assert "yr=1900,2019" in inner


def test_build_api_url_special_chars_encoded():
    url = build_api_url("Land Rover", "Range Rover", None, None)
    inner = _inner_url(url)
    assert "mk=Land%20Rover" in inner
    assert "md=Range%20Rover" in inner


def test_build_api_url_pagination():
    url = build_api_url("Ford", None, None, None, page=3, per_page=50)
    inner = _inner_url(url)
    assert "pg=3" in inner
    assert "rpp=50" in inner


def test_build_api_url_proxy_wrapper():
    url = build_api_url("Ford", None, None, None)
    assert url.startswith(PROXY_URL + "?endpoint=")
    assert url.endswith("&action=vms_data")
