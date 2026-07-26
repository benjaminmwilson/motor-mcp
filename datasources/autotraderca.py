"""Autotrader Canada (autotraderca) datasource — used vehicle listings via Next.js SSR."""

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

BASE_URL = "https://www.autotrader.ca"
DEFAULT_POSTAL_CODE = "V9C3M3"  # Colwood, BC


@dataclass
class AutotraderExtras:
    postal_code: str = field(
        default=DEFAULT_POSTAL_CODE,
        metadata={"description": "Canadian postal code for proximity sorting, e.g. 'V9C3M3'. Defaults to Colwood, BC if omitted."},
    )

_FIXED_PARAMS = [
    "cy=CA",
    "damaged_listing=exclude",
    "desc=0",
    "sort=standard",
    "atype=C",
]

# Maps the Forward Sortation Area (first letter of a Canadian postal code) to the
# Autotrader province path slug. Including the province in the URL path scopes the
# T10 promoted listing pool to that province, which dramatically improves location
# relevance and makes the per-page result count more predictable.
_PROVINCE_SLUG: dict[str, str] = {
    "A": "newfoundland-and-labrador",
    "B": "nova-scotia",
    "C": "prince-edward-island",
    "E": "new-brunswick",
    "G": "quebec",
    "H": "quebec",
    "J": "quebec",
    "K": "ontario",
    "L": "ontario",
    "M": "ontario",
    "N": "ontario",
    "P": "ontario",
    "R": "manitoba",
    "S": "saskatchewan",
    "T": "alberta",
    "V": "british-columbia",
    "X": "northwest-territories",
    "Y": "yukon",
}


def build_search_url(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    postal_code: str = DEFAULT_POSTAL_CODE,
    page: int = 1,
    per_page: int = 20,
) -> str:
    slug_parts = ["cars"]
    province = _PROVINCE_SLUG.get(postal_code[0].upper()) if postal_code else None
    if province:
        slug_parts.append(province)
    if make:
        slug_parts.append(make.lower().replace(" ", "-"))
        if model:
            slug_parts.append(model.lower().replace(" ", "-"))
    slug_parts.append("ot_used")

    params = list(_FIXED_PARAMS)
    params.append(f"loc={quote(postal_code, safe='')}")

    if year_min and year_max:
        params.append(f"yr={year_min}-{year_max}")
    elif year_min:
        params.append(f"yr={year_min}-{year_min}")
    elif year_max:
        params.append(f"yr=1900-{year_max}")

    if page > 1:
        params.append(f"pg={page}")
    if per_page != 20:
        params.append(f"size={per_page}")

    return BASE_URL + "/" + "/".join(slug_parts) + "?" + "&".join(params)


async def search_inventory(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
    extras: AutotraderExtras = AutotraderExtras(),
) -> dict:
    # Overfetch when year filters are active: Autotrader injects promoted listings
    # outside the requested range, so we request more to compensate before filtering.
    fetch_size = 100 if (year_min or year_max) else per_page
    url = build_search_url(make, model, year_min, year_max, extras.postal_code, page, fetch_size)
    async with AsyncSession(impersonate="chrome131") as client:
        resp = await client.get(url)
        resp.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        resp.text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Could not parse Autotrader Canada search results")

    page_props = json.loads(match.group(1))["props"]["pageProps"]
    total = page_props.get("numberOfResults", 0)

    vehicles = []
    for listing in page_props.get("listings", []):
        v = listing.get("vehicle", {})
        p = listing.get("price", {})
        loc = listing.get("location", {})

        vehicle_year = v.get("modelYear")
        if year_min and vehicle_year and vehicle_year < year_min:
            continue
        if year_max and vehicle_year and vehicle_year > year_max:
            continue

        mileage_str = v.get("mileageInKm", "")
        odometer = int(re.sub(r"[^\d]", "", mileage_str)) if mileage_str else None

        vehicles.append({
            "year": vehicle_year,
            "make": v.get("make"),
            "model": v.get("model"),
            "trim": v.get("modelVersionInput"),
            "condition": "Used" if v.get("offerType") == "U" else "New",
            "body_style": None,
            "drivetrain": None,
            "transmission": v.get("transmission"),
            "fuel_type": v.get("fuel"),
            "engine": None,
            "exterior_colour": None,
            "interior_colour": None,
            "odometer_km": odometer,
            "price_cad": p.get("priceRaw"),
            "msrp_cad": None,
            "stock": None,
            "vin": None,
            "days_on_lot": None,
            "city": loc.get("city"),
            "province": loc.get("provinceCode"),
            "url": listing.get("url"),
        })

    return {"total": total, "page": page, "per_page": per_page, "vehicles": vehicles[:per_page]}
