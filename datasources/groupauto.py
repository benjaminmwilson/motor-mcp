"""Group Auto Centre (groupauto) datasource — sm360 Inventory API.

Group Auto Centre has a single location in Colwood, BC.
"""

from dataclasses import dataclass
from typing import Optional

from curl_cffi.requests import AsyncSession

BASE_URL = "https://www.groupautocentre.com"
API_URL = (
    "https://service.vehicles.sm360.ca/inventory/vehicles"
    "?includeMetadata=true&location=BC&organizationId=7451&organizationUnitId=10263"
)
MAX_PAGE_SIZE = 100  # server-enforced cap; larger pageSize values return 400

API_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Referer": f"{BASE_URL}/en/used-inventory",
}

CITY = "Colwood"
PROVINCE = "BC"


@dataclass
class GroupAutoExtras:
    pass


def _build_payload(text_search: str, page_number: int, page_size: int) -> dict:
    return {
        "pagination": {"pageNumber": page_number, "pageSize": page_size},
        "paymentOptionRequest": {
            "cashDown": 0, "financePlan": None, "kmPerYearPlan": None, "lien": 0,
            "paymentFrequency": 52, "purchaseMethod": "cash", "saleType": "retail",
            "taxPlan": "standard", "term": 96, "tradeIn": 0, "priceIncreaseRollCount": 0,
        },
        "makePriority": [],
        "sortList": [{"direction": "ASC", "vehicleSortParameter": "WEBSITE_PRICE"}],
        "vehicle": {
            "makeIds": [], "modelIds": [], "catalogMakeIds": [], "catalogModelIds": [],
            "colanderSlug": "used", "soldDaysShown": 0, "textSearch": text_search,
            "frameStyleIds": [], "transmissionIds": [], "driveTrainIds": [], "fuelIds": [],
            "exteriorColorIds": [],
            "vehicleInventoryStatuses": ["FOR_SALE", "SOLD", "VIRTUAL", "ON_HOLD"],
        },
        "isMarketplaceRequest": False,
    }


def _build_vdp_url(v: dict) -> Optional[str]:
    make_slug = (v.get("make") or {}).get("slug")
    model_slug = (v.get("model") or {}).get("slug")
    year = v.get("year")
    vehicle_id = v.get("vehicleId")
    if not (make_slug and model_slug and year and vehicle_id):
        return None
    return (
        f"{BASE_URL}/en/used-inventory/{make_slug}/{model_slug}/"
        f"{year}-{make_slug}-{model_slug}-id{vehicle_id}"
    )


async def search_inventory(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
    extras: GroupAutoExtras = GroupAutoExtras(),
) -> dict:
    # The API has no year filter; the free-text search covers make/model and
    # the whole (small, single-location) inventory is cheap to page through.
    text_search = " ".join(part for part in (make, model) if part)

    all_vehicles: list[dict] = []
    async with AsyncSession(impersonate="chrome131") as client:
        page_number = 1
        while True:
            payload = _build_payload(text_search, page_number, MAX_PAGE_SIZE)
            resp = await client.post(API_URL, json=payload, headers=API_HEADERS)
            resp.raise_for_status()
            data = resp.json()
            all_vehicles.extend(data.get("inventoryVehicles", []))
            num_pages = data.get("pagination", {}).get("numberOfPages", 1)
            if page_number >= num_pages:
                break
            page_number += 1

    if year_min:
        all_vehicles = [v for v in all_vehicles if (v.get("year") or 0) >= year_min]
    if year_max:
        all_vehicles = [v for v in all_vehicles if (v.get("year") or 0) <= year_max]

    total = len(all_vehicles)
    start = (page - 1) * per_page
    page_vehicles = all_vehicles[start:start + per_page]

    vehicles = []
    for v in page_vehicles:
        make_info = v.get("make") or {}
        model_info = v.get("model") or {}
        body_style = v.get("bodyStyle") or {}
        fuel = v.get("fuel") or {}
        trim = v.get("trim") or {}
        engine = v.get("engine") or {}
        exterior = v.get("exteriorColor") or {}
        interior = v.get("interiorColor") or {}

        vehicles.append({
            "year": v.get("year"),
            "make": make_info.get("name"),
            "model": model_info.get("name"),
            "trim": trim.get("name"),
            "condition": "Used",
            "body_style": body_style.get("name"),
            "drivetrain": v.get("driveTrain"),
            "transmission": v.get("transmission"),
            "fuel_type": fuel.get("name"),
            "engine": engine.get("description"),
            "exterior_colour": exterior.get("colorEn"),
            "interior_colour": interior.get("colorEn"),
            "odometer_km": v.get("odometer"),
            "price_cad": v.get("salePrice"),
            "msrp_cad": v.get("listPrice"),
            "stock": v.get("stockNo"),
            "vin": v.get("serialNo"),
            "days_on_lot": v.get("daysInInventory"),
            "city": CITY,
            "province": PROVINCE,
            "url": _build_vdp_url(v),
        })

    return {"total": total, "page": page, "per_page": per_page, "vehicles": vehicles}
