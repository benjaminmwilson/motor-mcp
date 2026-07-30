"""Tests for the Autotrader Canada (autotraderca) datasource."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import datasources.autotraderca as atca
from datasources.autotraderca import (
    AutotraderExtras,
    DEFAULT_POSTAL_CODE,
    _resolve_ids,
    search_inventory,
)


# ---------------------------------------------------------------------------
# _resolve_ids
# ---------------------------------------------------------------------------

def _mock_taxonomy_response(cat: str):
    mock = MagicMock()
    mock.json.return_value = {
        "data": {"getFreeTextTaxonomyV2": {"items": [{"cat": cat}]}}
    }
    return mock


def _mock_sample_response(model_raw: int):
    mock = MagicMock()
    mock.json.return_value = {
        "data": {
            "search": {
                "listingsByQueryString": {
                    "listings": [{"details": {"vehicle": {"classification": {
                        "model": {"raw": model_raw, "formatted": "Civic"}
                    }}}}]
                }
            }
        }
    }
    return mock


@pytest.mark.asyncio
async def test_resolve_ids_make_and_model():
    """make+model returns (make_id, model_id) from two sequential calls."""
    atca._id_cache.clear()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(side_effect=[
        _mock_taxonomy_response("ma31gr200622"),
        _mock_sample_response(1775),
    ])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        make_id, model_id = await _resolve_ids("Honda", "Civic")

    assert make_id == 31
    assert model_id == 1775


@pytest.mark.asyncio
async def test_resolve_ids_make_only():
    """make-only returns (make_id, None) with a single taxonomy call."""
    atca._id_cache.clear()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(return_value=_mock_taxonomy_response("ma31gr200622"))

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        make_id, model_id = await _resolve_ids("Honda", None)

    assert make_id == 31
    assert model_id is None
    assert mock_session.post.call_count == 1  # no sample call needed


@pytest.mark.asyncio
async def test_resolve_ids_no_make():
    make_id, model_id = await _resolve_ids(None, None)
    assert make_id is None
    assert model_id is None


@pytest.mark.asyncio
async def test_resolve_ids_caches_result():
    atca._id_cache.clear()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(side_effect=[
        _mock_taxonomy_response("ma31gr200622"),
        _mock_sample_response(1775),
    ])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        await _resolve_ids("Honda", "Civic")
        result = await _resolve_ids("Honda", "Civic")  # should hit cache

    assert mock_session.post.call_count == 2  # taxonomy + sample, not 4
    assert result == (31, 1775)


@pytest.mark.asyncio
async def test_resolve_ids_network_error_returns_none():
    atca._id_cache.clear()
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(side_effect=Exception("connection error"))

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await _resolve_ids("Honda", "Civic")

    assert result == (None, None)


# ---------------------------------------------------------------------------
# search_inventory — happy path and edge cases
# ---------------------------------------------------------------------------

def _structured_listing(year: int, price: float = 20000.0, odometer: int = 50000,
                         model_name: str = "Civic") -> dict:
    return {
        "id": f"test-{year}-{odometer}",
        "details": {
            "webPage": f"https://www.autotrader.ca/offers/test-{year}",
            "vehicle": {
                "classification": {
                    "make": {"formatted": "Honda"},
                    "model": {"formatted": model_name},
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


def _structured_response(listings: list, total: int = None, page: int = 1,
                          page_size: int = 25) -> dict:
    return {
        "data": {
            "search": {
                "listings": {
                    "metadata": {
                        "currentPage": page,
                        "pageSize": page_size,
                        "totalItems": total if total is not None else len(listings),
                        "totalPages": max(1, (total or len(listings)) // page_size),
                    },
                    "listings": listings,
                }
            }
        }
    }


def _make_mock_session(nominatim_result=None, taxonomy_cat="ma31gr200622",
                       sample_model_raw=1775, search_response=None, status_code=200):
    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = nominatim_result or [
        {"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}
    ]

    taxonomy_resp = _mock_taxonomy_response(taxonomy_cat)
    sample_resp = _mock_sample_response(sample_model_raw)

    search_resp = MagicMock()
    search_resp.status_code = status_code
    search_resp.raise_for_status = MagicMock()
    if search_response is not None:
        search_resp.json.return_value = search_response

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=nominatim_resp)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, sample_resp, search_resp])
    return mock_session


@pytest.mark.asyncio
async def test_search_inventory_basic_result():
    atca._id_cache.clear()
    atca._geocode_cache.clear()

    data = _structured_response([_structured_listing(2012), _structured_listing(2013)], total=2)
    mock_session = _make_mock_session(search_response=data)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None,
                                        extras=AutotraderExtras(postal_code="M5H2N2"))

    assert result["total"] == 2
    assert len(result["vehicles"]) == 2
    assert result["vehicles"][0]["year"] == 2012
    assert result["vehicles"][0]["make"] == "Honda"
    assert result["vehicles"][0]["odometer_km"] == 50000
    assert result["vehicles"][0]["condition"] == "Used"


@pytest.mark.asyncio
async def test_search_inventory_year_filter_server_side():
    """With the structured API, year filtering is server-side — no client-side trimming."""
    atca._id_cache.clear()
    atca._geocode_cache.clear()

    listings = [_structured_listing(2010), _structured_listing(2013), _structured_listing(2015)]
    data = _structured_response(listings, total=3)
    mock_session = _make_mock_session(search_response=data)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", 2010, 2015,
                                        extras=AutotraderExtras(postal_code="M5H2N2"))

    # All server-returned listings pass through unchanged
    years = [v["year"] for v in result["vehicles"]]
    assert years == [2010, 2013, 2015]


@pytest.mark.asyncio
async def test_search_inventory_odometer_filter():
    """odometer_max_km in extras is sent as mileageInKm filter in the Vehicle_ input."""
    atca._id_cache.clear()
    atca._geocode_cache.clear()

    listings = [_structured_listing(2012, odometer=120000)]
    data = _structured_response(listings, total=1)
    mock_session = _make_mock_session(search_response=data)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None,
                                        extras=AutotraderExtras(postal_code="M5H2N2",
                                                                odometer_max_km=150000))

    assert len(result["vehicles"]) == 1
    # Verify the structured search POST included mileageInKm
    search_call = mock_session.post.call_args_list[-1]
    body = search_call.kwargs["json"]
    assert body["variables"]["vehicle"]["mileageInKm"] == {"to": 150000}


@pytest.mark.asyncio
async def test_search_inventory_pagination():
    """page and per_page are passed through to the structured API metadata."""
    atca._id_cache.clear()
    atca._geocode_cache.clear()

    data = _structured_response([_structured_listing(2014)], total=50, page=2)
    mock_session = _make_mock_session(search_response=data)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None, page=2, per_page=10,
                                        extras=AutotraderExtras(postal_code="M5H2N2"))

    assert result["page"] == 2
    search_call = mock_session.post.call_args_list[-1]
    body = search_call.kwargs["json"]
    assert body["variables"]["metadata"]["page"] == 2
    assert body["variables"]["metadata"]["size"] == 10


@pytest.mark.asyncio
async def test_search_inventory_lat_lon():
    """latitude/longitude bypass postal-code geocoding and reverse-geocode instead."""
    atca._id_cache.clear()
    atca._geocode_cache.clear()
    atca._reverse_geocode_cache.clear()

    data = _structured_response([_structured_listing(2012)], total=1)

    reverse_resp = MagicMock()
    reverse_resp.json.return_value = {"address": {"postcode": "V6B 1A1", "city": "Vancouver"}}

    taxonomy_resp = _mock_taxonomy_response("ma31gr200622")
    sample_resp = _mock_sample_response(1775)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.raise_for_status = MagicMock()
    search_resp.json.return_value = data

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=reverse_resp)
    mock_session.post = AsyncMock(side_effect=[taxonomy_resp, sample_resp, search_resp])

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory(
            "Honda", "Civic", None, None,
            extras=AutotraderExtras(latitude=49.2827, longitude=-123.1207),
        )

    assert len(result["vehicles"]) == 1
    # Reverse geocoding hit Nominatim's reverse endpoint with the given coordinates
    reverse_call = mock_session.get.call_args
    assert reverse_call.kwargs["params"]["lat"] == "49.2827"
    assert reverse_call.kwargs["params"]["lon"] == "-123.1207"
    # The resolved postal code / lat-lon are forwarded to the structured search
    search_call = mock_session.post.call_args_list[-1]
    body = search_call.kwargs["json"]
    assert body["variables"]["location"]["position"] == {"latitude": 49.2827, "longitude": -123.1207}
    assert body["variables"]["location"]["zip"] == "V6B1A1"


@pytest.mark.asyncio
async def test_search_inventory_lat_without_lon_raises():
    atca._id_cache.clear()
    atca._geocode_cache.clear()

    with pytest.raises(ValueError):
        await search_inventory("Honda", "Civic", None, None,
                                extras=AutotraderExtras(latitude=49.2827))


@pytest.mark.asyncio
async def test_search_inventory_auth_refresh_on_403():
    """On a 403, auth is refreshed and the request retried."""
    atca._id_cache.clear()
    atca._geocode_cache.clear()
    atca._cached_auth = "Basic oldtoken=="

    data = _structured_response([_structured_listing(2012)], total=1)

    nominatim_resp = MagicMock()
    nominatim_resp.json.return_value = [
        {"lat": "43.6488", "lon": "-79.3844", "address": {"city": "Toronto"}}
    ]

    taxonomy_resp = _mock_taxonomy_response("ma31gr200622")
    sample_resp = _mock_sample_response(1775)

    search_resp_403 = MagicMock()
    search_resp_403.status_code = 403
    search_resp_403.raise_for_status = MagicMock()

    search_resp_ok = MagicMock()
    search_resp_ok.status_code = 200
    search_resp_ok.raise_for_status = MagicMock()
    search_resp_ok.json.return_value = data

    homepage_resp = MagicMock()
    homepage_resp.text = '"/_next/static/chunks/main-abc123.js"'

    chunk_resp = MagicMock()
    chunk_resp.text = f'Basic {atca._AUTH_B64_PREFIX}NewTokenHere='

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    # GET order: nominatim, homepage, chunk
    mock_session.get = AsyncMock(side_effect=[nominatim_resp, homepage_resp, chunk_resp])
    # POST order: taxonomy, sample, first search (403), second search (200)
    mock_session.post = AsyncMock(
        side_effect=[taxonomy_resp, sample_resp, search_resp_403, search_resp_ok]
    )

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None,
                                        extras=AutotraderExtras(postal_code="M5H2N2"))

    assert len(result["vehicles"]) == 1
    assert atca._cached_auth == f"Basic {atca._AUTH_B64_PREFIX}NewTokenHere="
