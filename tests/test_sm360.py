"""Tests for the sm360 datasource."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from datasources.sm360 import SM360Extras, search_inventory


def _inventory_vehicle(vehicle_id: int, year: int, price: float = 20000.0,
                        odometer: int = 50000, make: str = "Honda",
                        model: str = "Civic") -> dict:
    return {
        "vehicleId": vehicle_id,
        "serialNo": f"VIN{vehicle_id}",
        "stockNo": f"S{vehicle_id}",
        "year": year,
        "make": {"slug": make.lower(), "name": make},
        "model": {"slug": model.lower().replace(" ", "-"), "name": model},
        "bodyStyle": {"name": "Sedan"},
        "exteriorColor": {"colorEn": "Red"},
        "interiorColor": {"colorEn": "Black"},
        "fuel": {"name": "Gasoline"},
        "trim": {"name": "EX"},
        "engine": {"description": "2.0L I4"},
        "salePrice": price,
        "listPrice": price + 1000,
        "odometer": odometer,
        "transmission": "Automatic",
        "driveTrain": "FWD",
        "daysInInventory": 10,
    }


def _mock_response(vehicles: list, page_number: int = 1, number_of_pages: int = 1):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "pagination": {"pageNumber": page_number, "pageSize": 100, "numberOfPages": number_of_pages},
        "totalElements": len(vehicles),
        "inventoryVehicles": vehicles,
    }
    return resp


def _mock_session(*responses):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = AsyncMock(side_effect=list(responses))
    return mock_session


def _mock_proxy_response(vehicles: list, page_number: int = 1, number_of_pages: int = 1):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "pagination": {
            "pageNumber": page_number, "pageSize": 100,
            "numberOfPages": number_of_pages, "numberOfItems": len(vehicles),
        },
        "vehicles": vehicles,
    }
    return resp


def _mock_proxy_session(*responses):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(side_effect=list(responses))
    return mock_session


@pytest.mark.asyncio
async def test_search_inventory_basic_result():
    vehicles = [_inventory_vehicle(1, 2018), _inventory_vehicle(2, 2020)]
    mock_session = _mock_session(_mock_response(vehicles))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory("Honda", "Civic", None, None, extras=SM360Extras(dealer="groupauto"))

    assert result["total"] == 2
    v = result["vehicles"][0]
    assert v["year"] == 2018
    assert v["city"] == "Colwood"
    assert v["province"] == "BC"
    assert v["dealer"] == "Group Auto Centre"
    assert v["url"] == (
        "https://www.groupautocentre.com/en/used-inventory/honda/civic/2018-honda-civic-id1"
    )


@pytest.mark.asyncio
async def test_search_inventory_uses_dealer_specific_org_ids():
    mock_session = _mock_session(_mock_response([]))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        await search_inventory(None, None, None, None, extras=SM360Extras(dealer="mbsherbrooke"))

    call = mock_session.post.call_args_list[0]
    assert "organizationId=1&organizationUnitId=8" in call.args[0]


@pytest.mark.asyncio
async def test_search_inventory_missing_dealer_raises():
    with pytest.raises(ValueError):
        await search_inventory(None, None, None, None, extras=SM360Extras(dealer=None))


@pytest.mark.asyncio
async def test_search_inventory_unknown_dealer_raises():
    with pytest.raises(ValueError):
        await search_inventory(None, None, None, None, extras=SM360Extras(dealer="not-a-real-dealer"))


@pytest.mark.asyncio
async def test_search_inventory_year_filter_client_side():
    """The sm360 API has no year filter, so filtering happens client-side."""
    vehicles = [_inventory_vehicle(1, 2010), _inventory_vehicle(2, 2015), _inventory_vehicle(3, 2020)]
    mock_session = _mock_session(_mock_response(vehicles))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, 2012, 2018, extras=SM360Extras(dealer="groupauto"))

    assert result["total"] == 1
    assert result["vehicles"][0]["year"] == 2015


@pytest.mark.asyncio
async def test_search_inventory_text_search_combines_make_and_model():
    mock_session = _mock_session(_mock_response([]))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        await search_inventory("Toyota", "Highlander", None, None, extras=SM360Extras(dealer="groupauto"))

    call = mock_session.post.call_args_list[0]
    assert call.kwargs["json"]["vehicle"]["textSearch"] == "Toyota Highlander"


@pytest.mark.asyncio
async def test_search_inventory_paginates_across_api_pages():
    """When the API reports multiple pages, all pages are fetched and merged."""
    page1 = [_inventory_vehicle(i, 2018) for i in range(1, 101)]
    page2 = [_inventory_vehicle(101, 2019)]
    mock_session = _mock_session(
        _mock_response(page1, page_number=1, number_of_pages=2),
        _mock_response(page2, page_number=2, number_of_pages=2),
    )

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, page=1, per_page=200, extras=SM360Extras(dealer="groupauto"))

    assert mock_session.post.call_count == 2
    assert result["total"] == 101


@pytest.mark.asyncio
async def test_search_inventory_client_side_pagination():
    vehicles = [_inventory_vehicle(i, 2018) for i in range(1, 6)]
    mock_session = _mock_session(_mock_response(vehicles))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, page=2, per_page=2, extras=SM360Extras(dealer="groupauto"))

    assert result["page"] == 2
    assert result["per_page"] == 2
    assert result["total"] == 5
    assert len(result["vehicles"]) == 2
    assert result["vehicles"][0]["vin"] == "VIN3"


@pytest.mark.asyncio
async def test_search_inventory_proxy_dealer_basic_result():
    vehicles = [_inventory_vehicle(1, 2018)]
    mock_session = _mock_proxy_session(_mock_proxy_response(vehicles))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, extras=SM360Extras(dealer="vidrives"))

    assert mock_session.get.call_count == 1
    assert mock_session.post.await_count == 0
    call = mock_session.get.call_args_list[0]
    assert call.args[0] == "https://www.vidrives.ca/en/used-inventory/api/listing"
    v = result["vehicles"][0]
    assert v["city"] == "Nanaimo"
    assert v["province"] == "BC"
    assert v["dealer"] == "VI Drives"


@pytest.mark.asyncio
async def test_search_inventory_proxy_dealer_client_side_make_filter():
    """The proxy route has no server-side text search, so make/model filtering happens client-side."""
    vehicles = [
        _inventory_vehicle(1, 2018, make="Honda", model="Civic"),
        _inventory_vehicle(2, 2019, make="Toyota", model="Corolla"),
    ]
    mock_session = _mock_proxy_session(_mock_proxy_response(vehicles))

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory("Toyota", None, None, None, extras=SM360Extras(dealer="vidrives"))

    assert result["total"] == 1
    assert result["vehicles"][0]["make"] == "Toyota"


@pytest.mark.asyncio
async def test_search_inventory_proxy_dealer_paginates_across_api_pages():
    page1 = [_inventory_vehicle(i, 2018) for i in range(1, 101)]
    page2 = [_inventory_vehicle(101, 2019)]
    mock_session = _mock_proxy_session(
        _mock_proxy_response(page1, page_number=1, number_of_pages=2),
        _mock_proxy_response(page2, page_number=2, number_of_pages=2),
    )

    with patch("datasources.sm360.AsyncSession", return_value=mock_session):
        result = await search_inventory(None, None, None, None, page=1, per_page=200, extras=SM360Extras(dealer="vidrives"))

    assert mock_session.get.call_count == 2
    assert result["total"] == 101
