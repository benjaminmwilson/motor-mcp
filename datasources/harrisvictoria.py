"""Harris Victoria Chrysler Dodge Jeep Ram (harrisvictoria) datasource — Convertus VMS API via WordPress proxy.

Same Convertus VMS backend as Galaxy Motors (datasources/galmo.py), just a
different dealer inventory ID. The site's used-vehicles page is a Vue SPA
(id="srp-app") that renders nothing server-side — all listings come from this
JSON API — so we call it directly instead of scraping HTML.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from curl_cffi.requests import AsyncSession


@dataclass
class HarrisVictoriaExtras:
    pass


INVENTORY_ID = "4397"
SEARCH_URL = "https://www.harrisvictoriachryslerdodgejeepram.ca/vehicles/used/"
PROXY_URL = "https://www.harrisvictoriachryslerdodgejeepram.ca/wp-content/plugins/convertus-vms/include/php/ajax-vehicles.php"
VMS_API_BASE = "https://vms.prod.convertus.rocks/api/filtering/"

API_HEADERS = {
    "Referer": SEARCH_URL,
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def build_api_url(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
) -> str:
    # Build the VMS API endpoint that the WP PHP proxy will forward to.
    # sc=used restricts to this dealer's used inventory, matching the site's
    # own /vehicles/used/ page.
    params = [f"cp={INVENTORY_ID}", "ln=en", "sc=used"]
    if make:
        params.append(f"mk={quote(make, safe='')}")
    if model:
        params.append(f"md={quote(model, safe='')}")
    if year_min and year_max:
        params.append(f"yr={year_min},{year_max}")
    elif year_min:
        params.append(f"yr={year_min},{year_min}")
    elif year_max:
        params.append(f"yr=1900,{year_max}")
    params.extend([f"pg={page}", f"rpp={per_page}", "st=price,asc"])

    endpoint = VMS_API_BASE + "?" + "&".join(params)
    return f"{PROXY_URL}?endpoint={quote(endpoint)}&action=vms_data"


async def search_inventory(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
    extras: HarrisVictoriaExtras = HarrisVictoriaExtras(),
) -> dict:
    # The VMS API's "md" filter requires an exact match against its internal model
    # names, so passing a model straight through would silently match nothing.
    # Fetch by make/year only and filter by model as a case-insensitive substring
    # client-side instead (same approach as galmo.py).
    fetch_per_page = 100 if model else per_page
    fetch_page = 1 if model else page
    url = build_api_url(make, None, year_min, year_max, fetch_page, fetch_per_page)
    async with AsyncSession(impersonate="chrome131") as client:
        # Visit SRP first to pick up session cookies before the AJAX call
        await client.get(SEARCH_URL)
        resp = await client.get(url, headers=API_HEADERS)
        resp.raise_for_status()

    data = resp.json()
    results = data.get("results", [])

    if model:
        results = [v for v in results if model.lower() in (v.get("model") or "").lower()]
        total = len(results)
        start = (page - 1) * per_page
        results = results[start:start + per_page]
    else:
        total = data.get("summary", {}).get("total_vehicles", 0)

    vehicles = []
    for v in results:
        company = v.get("company_data") or {}
        vehicles.append({
            "year": v.get("year"),
            "make": v.get("make"),
            "model": v.get("model"),
            "trim": v.get("trim"),
            "condition": "Demo" if v.get("demo") else (v.get("sale_class") or "Used"),
            "body_style": v.get("body_style"),
            "drivetrain": v.get("drive_train"),
            "transmission": v.get("transmission"),
            "fuel_type": v.get("fuel_type"),
            "engine": v.get("engine"),
            "exterior_colour": v.get("exterior_color"),
            "interior_colour": v.get("interior_color"),
            "odometer_km": v.get("odometer"),
            "price_cad": v.get("internet_price") or v.get("asking_price"),
            "msrp_cad": v.get("msrp"),
            "stock": v.get("stock_number"),
            "vin": v.get("vin"),
            "days_on_lot": v.get("days_on_lot"),
            "city": company.get("company_city"),
            "province": company.get("company_province"),
            "url": v.get("vdp_url"),
        })

    return {"total": total, "page": page, "per_page": per_page, "vehicles": vehicles}
