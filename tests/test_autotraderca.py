"""Tests for the Autotrader Canada (autotraderca) datasource."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import datasources.autotraderca as atca
from datasources.autotraderca import (
    AutotraderExtras,
    DEFAULT_POSTAL_CODE,
    _build_qs,
    _resolve_cat,
    search_inventory,
)


# ---------------------------------------------------------------------------
# _build_qs
# ---------------------------------------------------------------------------

def test_build_qs_cat_code():
    qs = _build_qs("ma31gr200622", None, None, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "cat=ma31gr200622" in qs
    assert "mmmv=" not in qs


def test_build_qs_mmmv_string():
    qs = _build_qs("31|||", None, None, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "mmmv=31%7C%7C%7C" in qs or "mmmv=31|||" in qs
    assert "cat=" not in qs


def test_build_qs_no_make():
    qs = _build_qs(None, None, None, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "cat=" not in qs
    assert "mmmv=" not in qs


def test_build_qs_year_range():
    qs = _build_qs(None, 2010, 2015, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "yr=2010-2015" in qs


def test_build_qs_year_min_only():
    qs = _build_qs(None, 2018, None, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "yr=2018-2018" in qs


def test_build_qs_year_max_only():
    qs = _build_qs(None, None, 2020, 43.6, -79.4, "M5H2N2", "Toronto", "ON", 1, 25)
    assert "yr=1900-2020" in qs


def test_build_qs_fixed_params():
    qs = _build_qs(None, None, None, 48.43, -123.5, "V9C3M3", "Victoria", "BC", 2, 50)
    assert "lat=48.430000" in qs
    assert "lon=-123.500000" in qs
    assert "zipr=100" in qs
    assert "offer=U" in qs
    assert "cy=CA" in qs
    assert "damaged_listing=exclude" in qs
    assert "atype=C" in qs
    assert "pg=2" in qs
    assert "size=50" in qs


# ---------------------------------------------------------------------------
# _resolve_cat
# ---------------------------------------------------------------------------

def _mock_graphql_response(cat: str):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "getFreeTextTaxonomyV2": {
                "items": [{"cat": cat, "displayName": "Honda Civic"}]
            }
        }
    }
    return mock_resp


@pytest.mark.asyncio
async def test_resolve_cat_make_and_model():
    """make+model returns the full cat code."""
    atca._taxonomy_cache.clear()
    mock_resp = _mock_graphql_response("ma31gr200622")
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(return_value=mock_resp)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await _resolve_cat("Honda", "Civic")

    assert result == "ma31gr200622"


@pytest.mark.asyncio
async def test_resolve_cat_make_only():
    """make-only strips the model group and returns 'makeId|||'."""
    atca._taxonomy_cache.clear()
    mock_resp = _mock_graphql_response("ma31gr200622")
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(return_value=mock_resp)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await _resolve_cat("Honda", None)

    assert result == "31|||"


@pytest.mark.asyncio
async def test_resolve_cat_no_make():
    result = await _resolve_cat(None, None)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_cat_caches_result():
    atca._taxonomy_cache.clear()
    mock_resp = _mock_graphql_response("ma31gr200622")
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(return_value=mock_resp)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        await _resolve_cat("Honda", "Civic")
        await _resolve_cat("Honda", "Civic")

    assert mock_session.post.call_count == 1


@pytest.mark.asyncio
async def test_resolve_cat_network_error_returns_none():
    atca._taxonomy_cache.clear()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(side_effect=Exception("connection error"))

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await _resolve_cat("Honda", "Civic")

    assert result is None


# ---------------------------------------------------------------------------
# search_inventory — happy path and edge cases
# ---------------------------------------------------------------------------

def _gql_listing(year: int, price: float = 20000.0, odometer: int = 50000) -> dict:
    return {
        "id": f"test-{year}",
        "details": {
            "webPage": f"https://www.autotrader.ca/offers/test-{year}",
            "vehicle": {
                "classification": {
                    "make": {"formatted": "Honda"},
                    "model": {"formatted": "Civic"},
                    "modelVersionInput": "EX",
                    "modelYear": year,
                },
                "condition": {"mileageInKm": {"raw": odometer}},
                "bodyColor": {"formatted": "Red"},
                "bodyType": {"formatted": "Sedan"},
                "fuels": {"primary": {"type": {"formatted": "Gasoline"}}},
                "engine": {
                    "transmissionType": {"formatted": "Automatic"},
                    "driveTrain": {"formatted": "FWD"},
                },
                "usageState": "U",
            },
            "prices": {"public": {"amountInEUR": {"raw": price}}},
            "location": {"city": "Toronto", "distanceToSearchLocationInKm": 5},
        },
    }


def _gql_response(listings: list, total: int = None, page: int = 1, page_size: int = 25) -> dict:
    return {
        "data": {
            "search": {
                "listingsByQueryString": {
                    "metadata": {
                        "currentPage": page,
                        "pageSize": page_size,
                        "totalItems": total if total is not None else len(listings),
                        "totalPages": 1,
                    },
                    "listings": listings,
                }
            }
        }
    }


def _make_mock_session(post_response=None, get_response=None, status_code=200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    if post_response is not None:
        mock_resp.json.return_value = post_response

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(return_value=mock_resp)
    if get_response is not None:
        get_mock = MagicMock()
        get_mock.json.return_value = get_response
        mock_session.get = AsyncMock(return_value=get_mock)
    return mock_session


@pytest.mark.asyncio
async def test_search_inventory_basic_result():
    atca._taxonomy_cache.clear()
    atca._geocode_cache.clear()

    gql_data = _gql_response([_gql_listing(2012), _gql_listing(2013)], total=2)

    # geocode GET → nominatim result; taxonomy POST → cat; search POST → listings
    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = [{"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}]

    taxonomy_resp = MagicMock()
    taxonomy_resp.json.return_value = {
        "data": {"getFreeTextTaxonomyV2": {"items": [{"cat": "ma31gr200622", "displayName": "Honda Civic"}]}}
    }

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.raise_for_status = MagicMock()
    search_resp.json.return_value = gql_data

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=nominatim_resp)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, search_resp])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None, extras=AutotraderExtras(postal_code="M5H2N2"))

    assert result["total"] == 2
    assert len(result["vehicles"]) == 2
    assert result["vehicles"][0]["year"] == 2012
    assert result["vehicles"][0]["make"] == "Honda"
    assert result["vehicles"][0]["odometer_km"] == 50000
    assert result["vehicles"][0]["condition"] == "Used"


@pytest.mark.asyncio
async def test_search_inventory_year_filter_drops_promoted():
    """T10 promoted listings outside the year range are dropped client-side."""
    atca._taxonomy_cache.clear()
    atca._geocode_cache.clear()

    listings = [
        _gql_listing(2008),  # below year_min — drop
        _gql_listing(2010),  # in range
        _gql_listing(2013),  # in range
        _gql_listing(2016),  # above year_max — drop
    ]
    gql_data = _gql_response(listings, total=4)

    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = [{"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}]

    taxonomy_resp = MagicMock()
    taxonomy_resp.json.return_value = {
        "data": {"getFreeTextTaxonomyV2": {"items": [{"cat": "ma31gr200622", "displayName": "Honda Civic"}]}}
    }

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.raise_for_status = MagicMock()
    search_resp.json.return_value = gql_data

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=nominatim_resp)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, search_resp])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", 2010, 2015, extras=AutotraderExtras(postal_code="M5H2N2"))

    years = [v["year"] for v in result["vehicles"]]
    assert 2008 not in years
    assert 2016 not in years
    assert 2010 in years
    assert 2013 in years


@pytest.mark.asyncio
async def test_search_inventory_no_year_filter_keeps_all():
    atca._taxonomy_cache.clear()
    atca._geocode_cache.clear()

    gql_data = _gql_response([_gql_listing(2010), _gql_listing(2020)], total=2)

    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = [{"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}]

    taxonomy_resp = MagicMock()
    taxonomy_resp.json.return_value = {
        "data": {"getFreeTextTaxonomyV2": {"items": [{"cat": "ma31gr200622", "displayName": "Honda Civic"}]}}
    }

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.raise_for_status = MagicMock()
    search_resp.json.return_value = gql_data

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=nominatim_resp)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, search_resp])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None, extras=AutotraderExtras(postal_code="M5H2N2"))

    assert len(result["vehicles"]) == 2


@pytest.mark.asyncio
async def test_search_inventory_auth_refresh_on_403():
    """On a 403, auth is refreshed and the request retried."""
    atca._taxonomy_cache.clear()
    atca._geocode_cache.clear()
    atca._cached_auth = "Basic oldtoken=="

    gql_data = _gql_response([_gql_listing(2012)], total=1)

    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = [{"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}]

    taxonomy_resp = MagicMock()
    taxonomy_resp.json.return_value = {
        "data": {"getFreeTextTaxonomyV2": {"items": [{"cat": "ma31gr200622", "displayName": "Honda Civic"}]}}
    }

    # First search POST → 403; second → 200
    search_resp_403 = MagicMock()
    search_resp_403.status_code = 403
    search_resp_403.raise_for_status = MagicMock()

    search_resp_ok = MagicMock()
    search_resp_ok.status_code = 200
    search_resp_ok.raise_for_status = MagicMock()
    search_resp_ok.json.return_value = gql_data

    # Homepage GET for auth refresh
    homepage_resp = MagicMock()
    homepage_resp.text = '"/_next/static/chunks/main-abc123.js"'

    chunk_resp = MagicMock()
    chunk_resp.text = f'Basic {atca._AUTH_B64_PREFIX}NewTokenHere='

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    # GET calls: nominatim, homepage, chunk
    mock_session.get = AsyncMock(side_effect=[nominatim_resp, homepage_resp, chunk_resp])
    # POST calls: taxonomy, first search (403), second search (200 after refresh)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, search_resp_403, search_resp_ok])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None, extras=AutotraderExtras(postal_code="M5H2N2"))

    assert len(result["vehicles"]) == 1
    assert atca._cached_auth == f"Basic {atca._AUTH_B64_PREFIX}NewTokenHere="
