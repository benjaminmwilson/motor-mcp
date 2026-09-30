"""Tests for the Harris Victoria Chrysler Dodge Jeep Ram (harrisvictoria) datasource."""

from urllib.parse import unquote
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datasources.harrisvictoria import (
    HarrisVictoriaExtras,
    PROXY_URL,
    SEARCH_URL,
    build_api_url,
    search_inventory,
)


def _inner_url(full_url: str) -> str:
    """Extract and decode the VMS API URL from the proxy wrapper."""
    after_endpoint = full_url.split("endpoint=", 1)[1]
    encoded = after_endpoint.split("&action=", 1)[0]
    return unquote(encoded)


def test_build_api_url_all_filters():
    url = build_api_url("Jeep", "Wrangler", 2018, 2022)
    inner = _inner_url(url)
    assert "mk=Jeep" in inner
    assert "md=Wrangler" in inner
    assert "yr=2018,2022" in inner
    assert "pg=1" in inner
    assert "rpp=25" in inner


def test_build_api_url_always_scopes_to_used():
    url = build_api_url(None, None, None, None)
    inner = _inner_url(url)
    assert "sc=used" in inner


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


def _vms_vehicle(vehicle_id: int, year: int, make: str = "Jeep", model: str = "Wrangler",
                  price: float = 30000.0, odometer: int = 50000, demo: int = 0) -> dict:
    return {
        "vehicle_id": vehicle_id,
        "vin": f"VIN{vehicle_id}",
        "stock_number": f"S{vehicle_id}",
        "year": year,
        "make": make,
        "model": model,
        "trim": "Sport",
        "sale_class": "Used",
        "demo": demo,
        "body_style": "SUV",
        "drive_train": "4x4",
        "transmission": "Automatic",
        "fuel_type": "Gas",
        "engine": "3.6L V6",
        "exterior_color": "Black",
        "interior_color": "Grey",
        "odometer": odometer,
        "internet_price": price,
        "asking_price": price,
        "msrp": price + 2000,
        "days_on_lot": 10,
        "vdp_url": f"https://www.harrisvictoriachryslerdodgejeepram.ca/vehicles/{year}/{make}/{model}/id{vehicle_id}",
        "company_data": {"company_city": "Victoria", "company_province": "BC"},
    }


def _mock_response(total_vehicles: int, vehicles: list):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "summary": {"total_vehicles": total_vehicles},
        "results": vehicles,
    }
    return resp


def _noop_response():
    """Response for the warm-up GET to SEARCH_URL, whose body is never read."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    return resp


def _mock_session(api_response):
    """A session whose first GET (the SEARCH_URL warm-up) is a no-op and
    whose second GET (the VMS API call) returns api_response."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(side_effect=[_noop_response(), api_response])
    return mock_session


@pytest.mark.asyncio
async def test_search_inventory_basic_result():
    vehicles = [_vms_vehicle(1, 2020), _vms_vehicle(2, 2019)]
    mock_session = _mock_session(_mock_response(2, vehicles))

    with patch("datasources.harrisvictoria.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, extras=HarrisVictoriaExtras())

    assert result["total"] == 2
    v = result["vehicles"][0]
    assert v["year"] == 2020
    assert v["make"] == "Jeep"
    assert v["model"] == "Wrangler"
    assert v["condition"] == "Used"
    assert v["city"] == "Victoria"
    assert v["province"] == "BC"
    assert v["vin"] == "VIN1"
    assert v["url"] == "https://www.harrisvictoriachryslerdodgejeepram.ca/vehicles/2020/Jeep/Wrangler/id1"


@pytest.mark.asyncio
async def test_search_inventory_demo_flag_sets_condition():
    vehicles = [_vms_vehicle(1, 2020, demo=1), _vms_vehicle(2, 2019, demo=0)]
    mock_session = _mock_session(_mock_response(2, vehicles))

    with patch("datasources.harrisvictoria.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, extras=HarrisVictoriaExtras())

    assert result["vehicles"][0]["condition"] == "Demo"
    assert result["vehicles"][1]["condition"] == "Used"


@pytest.mark.asyncio
async def test_search_inventory_model_filter_is_client_side_substring():
    """The VMS API's "md" filter requires an exact internal model name, so
    search_inventory fetches by make/year only and filters by model client-side."""
    vehicles = [
        _vms_vehicle(1, 2020, model="Wrangler"),
        _vms_vehicle(2, 2020, model="Wrangler Unlimited"),
        _vms_vehicle(3, 2020, model="Cherokee"),
    ]
    mock_session = _mock_session(_mock_response(3, vehicles))

    with patch("datasources.harrisvictoria.AsyncSession", return_value=mock_session):
        result = await search_inventory("Jeep", "Wrangler", None, None, extras=HarrisVictoriaExtras())

    assert result["total"] == 2
    assert {v["model"] for v in result["vehicles"]} == {"Wrangler", "Wrangler Unlimited"}

    call = mock_session.get.call_args_list[1]
    assert "md=" not in unquote(call.args[0])


@pytest.mark.asyncio
async def test_search_inventory_no_model_uses_server_reported_total():
    vehicles = [_vms_vehicle(1, 2020)]
    mock_session = _mock_session(_mock_response(196, vehicles))

    with patch("datasources.harrisvictoria.AsyncSession", return_value=mock_session):
        result = await search_inventory("Jeep", None, None, None, extras=HarrisVictoriaExtras())

    assert result["total"] == 196
    assert len(result["vehicles"]) == 1


@pytest.mark.asyncio
async def test_search_inventory_visits_search_url_before_api_call():
    mock_session = _mock_session(_mock_response(0, []))

    with patch("datasources.harrisvictoria.AsyncSession", return_value=mock_session):
        await search_inventory(None, None, None, None, extras=HarrisVictoriaExtras())

    first_call = mock_session.get.call_args_list[0]
    assert first_call.args[0] == SEARCH_URL


@pytest.mark.asyncio
async def test_extras_dataclass_has_no_fields():
    assert HarrisVictoriaExtras().__dict__ == {}
