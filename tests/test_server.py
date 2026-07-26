"""Basic tests for server.py."""

from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import pytest

import server
from server import build_api_url, call_tool


# ---------------------------------------------------------------------------
# build_api_url
# ---------------------------------------------------------------------------

def _inner_url(full_url: str) -> str:
    """Extract and decode the VMS API URL from the proxy wrapper."""
    # full_url = PROXY_URL?endpoint=<encoded>&action=vms_data
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
    assert url.startswith(server.PROXY_URL + "?endpoint=")
    assert url.endswith("&action=vms_data")


# ---------------------------------------------------------------------------
# call_tool — validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_no_filters_returns_error():
    result = await call_tool("search_vehicles", {})
    assert len(result) == 1
    assert "at least one" in result[0].text.lower()


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_raises():
    with pytest.raises(ValueError, match="Unknown tool"):
        await call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_call_tool_per_page_capped_at_100():
    mock_result = {"total": 0, "page": 1, "per_page": 100, "vehicles": []}
    with patch("server.search_inventory", new=AsyncMock(return_value=mock_result)) as mock:
        await call_tool("search_vehicles", {"make": "Ford", "per_page": 200})
    _, kwargs = mock.call_args
    assert mock.call_args[0][5] == 100  # per_page positional arg


@pytest.mark.asyncio
async def test_call_tool_string_page_coerced():
    mock_result = {"total": 0, "page": 2, "per_page": 25, "vehicles": []}
    with patch("server.search_inventory", new=AsyncMock(return_value=mock_result)) as mock:
        await call_tool("search_vehicles", {"make": "Ford", "page": "2"})
    assert mock.call_args[0][4] == 2  # page positional arg


# ---------------------------------------------------------------------------
# call_tool — output formatting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_no_results_message():
    mock_result = {"total": 0, "page": 1, "per_page": 25, "vehicles": []}
    with patch("server.search_inventory", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles", {"make": "Lada"})
    assert "No vehicles found" in result[0].text


@pytest.mark.asyncio
async def test_call_tool_formats_vehicle():
    vehicle = {
        "year": 2021, "make": "Toyota", "model": "Camry", "trim": "XSE",
        "condition": "Used", "exterior_colour": "Red", "interior_colour": "Black",
        "odometer_km": 45000, "price_cad": 32500, "msrp_cad": None,
        "drivetrain": "FWD", "transmission": "Automatic", "fuel_type": "Gasoline",
        "engine": "2.5L I4", "body_style": "Sedan", "stock": "A1234",
        "vin": "1HGBH41JXMN109186", "days_on_lot": 10, "url": "https://example.com/camry",
    }
    mock_result = {"total": 1, "page": 1, "per_page": 25, "vehicles": [vehicle]}
    with patch("server.search_inventory", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles", {"make": "Toyota"})
    text = result[0].text
    assert "2021 Toyota Camry XSE" in text
    assert "$32,500" in text
    assert "45,000 km" in text
    assert "Total matching: 1" in text


@pytest.mark.asyncio
async def test_call_tool_missing_price_and_odometer_shown_as_na():
    vehicle = {
        "year": 2020, "make": "Ford", "model": "F-150", "trim": None,
        "condition": "Used", "exterior_colour": "Blue", "interior_colour": None,
        "odometer_km": None, "price_cad": None, "msrp_cad": None,
        "drivetrain": "4WD", "transmission": "Automatic", "fuel_type": "Gasoline",
        "engine": "5.0L V8", "body_style": "Truck", "stock": "B5678",
        "vin": "1FTFW1E81NFA00001", "days_on_lot": 5, "url": "https://example.com/f150",
    }
    mock_result = {"total": 1, "page": 1, "per_page": 25, "vehicles": [vehicle]}
    with patch("server.search_inventory", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles", {"make": "Ford"})
    text = result[0].text
    assert "N/A" in text
