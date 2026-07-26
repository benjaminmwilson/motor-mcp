"""Tests for the MCP server dispatch and output formatting."""

from unittest.mock import AsyncMock, patch

import pytest

from datasources import DATASOURCES
from server import call_tool


# ---------------------------------------------------------------------------
# call_tool — validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_no_filters_returns_error():
    result = await call_tool("search_vehicles_galmo", {})
    assert len(result) == 1
    assert "at least one" in result[0].text.lower()


@pytest.mark.asyncio
async def test_call_tool_unknown_tool_raises():
    with pytest.raises(ValueError, match="Unknown tool"):
        await call_tool("nonexistent", {})


@pytest.mark.asyncio
async def test_call_tool_per_page_capped_at_100():
    mock_result = {"total": 0, "page": 1, "per_page": 100, "vehicles": []}
    with patch.object(DATASOURCES["galmo"], "search", new=AsyncMock(return_value=mock_result)) as mock:
        await call_tool("search_vehicles_galmo", {"make": "Ford", "per_page": 200})
    assert mock.call_args[0][5] == 100  # per_page positional arg


@pytest.mark.asyncio
async def test_call_tool_string_page_coerced():
    mock_result = {"total": 0, "page": 2, "per_page": 25, "vehicles": []}
    with patch.object(DATASOURCES["galmo"], "search", new=AsyncMock(return_value=mock_result)) as mock:
        await call_tool("search_vehicles_galmo", {"make": "Ford", "page": "2"})
    assert mock.call_args[0][4] == 2  # page positional arg


# ---------------------------------------------------------------------------
# call_tool — output formatting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_no_results_message():
    mock_result = {"total": 0, "page": 1, "per_page": 25, "vehicles": []}
    with patch.object(DATASOURCES["galmo"], "search", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles_galmo", {"make": "Lada"})
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
    with patch.object(DATASOURCES["galmo"], "search", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles_galmo", {"make": "Toyota"})
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
    with patch.object(DATASOURCES["galmo"], "search", new=AsyncMock(return_value=mock_result)):
        result = await call_tool("search_vehicles_galmo", {"make": "Ford"})
    text = result[0].text
    assert "N/A" in text
