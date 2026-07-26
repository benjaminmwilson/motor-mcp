"""Tests for the Autotrader Canada (autotraderca) datasource."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from datasources.autotraderca import build_search_url, search_inventory, BASE_URL, DEFAULT_POSTAL_CODE


def test_build_search_url_make_and_model():
    url = build_search_url("Toyota", "Camry", None, None, postal_code="M5H2N2")
    assert "/cars/ontario/toyota/camry/ot_used" in url
    assert "loc=M5H2N2" in url


def test_build_search_url_make_only():
    url = build_search_url("Ford", None, None, None, postal_code="M5H2N2")
    assert "/cars/ontario/ford/ot_used" in url


def test_build_search_url_no_make_no_model():
    url = build_search_url(None, None, None, None, postal_code="M5H2N2")
    assert "/cars/ontario/ot_used" in url


def test_build_search_url_bc_postal_code():
    url = build_search_url("Honda", "Civic", None, None, postal_code="V9C3M3")
    assert "/cars/british-columbia/honda/civic/ot_used" in url


def test_build_search_url_alberta_postal_code():
    url = build_search_url("Ford", None, None, None, postal_code="T2P1J9")
    assert "/cars/alberta/ford/ot_used" in url


def test_build_search_url_year_range():
    url = build_search_url(None, None, 2018, 2022)
    assert "yr=2018-2022" in url


def test_build_search_url_year_min_only():
    url = build_search_url(None, None, 2020, None)
    assert "yr=2020-2020" in url


def test_build_search_url_year_max_only():
    url = build_search_url(None, None, None, 2019)
    assert "yr=1900-2019" in url


def test_build_search_url_pagination():
    url = build_search_url("Honda", None, None, None, page=3)
    assert "pg=3" in url


def test_build_search_url_page_1_omitted():
    url = build_search_url("Honda", None, None, None, page=1)
    assert "pg=" not in url


def test_build_search_url_per_page_non_default():
    url = build_search_url("Honda", None, None, None, per_page=50)
    assert "size=50" in url


def test_build_search_url_default_per_page_omitted():
    url = build_search_url("Honda", None, None, None, per_page=20)
    assert "size=" not in url


def test_build_search_url_postal_code():
    url = build_search_url("Ford", None, None, None, postal_code="K1A0A9")
    assert "loc=K1A0A9" in url


def test_build_search_url_multiword_make_slugified():
    url = build_search_url("Land Rover", "Range Rover", None, None, postal_code="M5H2N2")
    assert "/cars/ontario/land-rover/range-rover/ot_used" in url


def test_build_search_url_fixed_params():
    url = build_search_url("Ford", None, None, None)
    assert "cy=CA" in url
    assert "damaged_listing=exclude" in url
    assert "atype=C" in url
    assert url.startswith(BASE_URL)


# ---------------------------------------------------------------------------
# search_inventory — client-side year filtering
# ---------------------------------------------------------------------------

def _make_next_data(listings: list, total: int = None) -> str:
    """Build a minimal __NEXT_DATA__ HTML snippet for mocking."""
    payload = {
        "props": {
            "pageProps": {
                "numberOfResults": total if total is not None else len(listings),
                "listings": listings,
            }
        }
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def _listing(year: int, price: int = 20000, odometer: str = "50,000 km") -> dict:
    return {
        "vehicle": {
            "modelYear": year,
            "make": "Chevrolet",
            "model": "Camaro",
            "modelVersionInput": "1LT",
            "offerType": "U",
            "transmission": "Automatic",
            "fuel": "Gasoline",
            "mileageInKm": odometer,
        },
        "price": {"priceRaw": price},
        "location": {"city": "Toronto", "provinceCode": "ON"},
        "url": f"https://www.autotrader.ca/offers/camaro-{year}",
    }


@pytest.mark.asyncio
async def test_search_inventory_filters_out_of_range_years():
    """Autotrader injects promoted listings outside the year filter; we drop them."""
    html = _make_next_data([
        _listing(2009),  # below year_min — should be dropped
        _listing(2010),  # in range
        _listing(2013),  # in range
        _listing(2015),  # in range
        _listing(2016),  # above year_max — should be dropped
    ], total=3)

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=mock_resp)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Chevrolet", "Camaro", 2010, 2015)

    assert len(result["vehicles"]) == 3
    years = [v["year"] for v in result["vehicles"]]
    assert years == [2010, 2013, 2015]


@pytest.mark.asyncio
async def test_search_inventory_no_year_filter_keeps_all():
    html = _make_next_data([_listing(2010), _listing(2020)])

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=mock_resp)

    with patch("datasources.autotraderca.AsyncSession", return_value=mock_session):
        result = await search_inventory("Chevrolet", "Camaro", None, None)

    assert len(result["vehicles"]) == 2
