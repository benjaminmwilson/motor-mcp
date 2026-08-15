"""sm360 datasource — shared inventory API behind many Canadian dealer sites.

sm360 (part of 360.Agency) powers dealer websites for many independent
Canadian dealers, keyed by organizationId/organizationUnitId. Different
dealer sites use different front-end templates (some call the inventory API
directly from the browser, others proxy it through a first-party route), but
the underlying API at service.vehicles.sm360.ca is identical for all of
them. New dealers can be added to REGISTRY without writing new code: load
the dealer's used-inventory page and search its HTML for
`"organizationId":<id>,"organizationUnitId":<id>` (and `addressLocality`/
`addressRegion` for city/province).
"""

from dataclasses import dataclass, field
from typing import Optional

from curl_cffi.requests import AsyncSession


@dataclass(frozen=True)
class Dealer:
    name: str
    base_url: str
    organization_id: int
    organization_unit_id: int
    city: str
    province: str


REGISTRY: dict[str, Dealer] = {
    "groupauto": Dealer(
        name="Group Auto Centre",
        base_url="https://www.groupautocentre.com",
        organization_id=7451,
        organization_unit_id=10263,
        city="Colwood",
        province="BC",
    ),
    "dixieinfiniti": Dealer(
        name="401 Dixie Infiniti",
        base_url="https://www.401dixieinfiniti.ca",
        organization_id=481,
        organization_unit_id=1687,
        city="Mississauga",
        province="ON",
    ),
    "mbnorthvancouver": Dealer(
        name="Mercedes-Benz North Vancouver",
        base_url="https://www.mercedes-benz-north-vancouver.ca",
        organization_id=481,
        organization_unit_id=1816,
        city="North Vancouver",
        province="BC",
    ),
    "mbsherbrooke": Dealer(
        name="Mercedes-Benz de Sherbrooke",
        base_url="https://www.mercedes-benz-sherbrooke.ca",
        organization_id=1,
        organization_unit_id=8,
        city="Sherbrooke",
        province="QC",
    ),
}

API_URL_TEMPLATE = (
    "https://service.vehicles.sm360.ca/inventory/vehicles"
    "?includeMetadata=true&location={province}"
    "&organizationId={organization_id}&organizationUnitId={organization_unit_id}"
)
MAX_PAGE_SIZE = 100  # server-enforced cap; larger pageSize values return 400


@dataclass
class SM360Extras:
    dealer: Optional[str] = field(
        default=None,
        metadata={
            "description": f"Which dealer to search. One of: {', '.join(sorted(REGISTRY))}",
        },
    )


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


def _build_vdp_url(base_url: str, v: dict) -> Optional[str]:
    make_slug = (v.get("make") or {}).get("slug")
    model_slug = (v.get("model") or {}).get("slug")
    year = v.get("year")
    vehicle_id = v.get("vehicleId")
    if not (make_slug and model_slug and year and vehicle_id):
        return None
    return (
        f"{base_url}/en/used-inventory/{make_slug}/{model_slug}/"
        f"{year}-{make_slug}-{model_slug}-id{vehicle_id}"
    )


async def search_inventory(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
    extras: SM360Extras = SM360Extras(),
) -> dict:
    if not extras.dealer or extras.dealer not in REGISTRY:
        raise ValueError(
            f"extras.dealer must be one of: {', '.join(sorted(REGISTRY))} "
            f"(got {extras.dealer!r})"
        )
    dealer = REGISTRY[extras.dealer]

    api_url = API_URL_TEMPLATE.format(
        province=dealer.province,
        organization_id=dealer.organization_id,
        organization_unit_id=dealer.organization_unit_id,
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{dealer.base_url}/en/used-inventory",
    }

    # The API has no year filter; the free-text search covers make/model and
    # a single dealer's inventory is cheap to page through in full.
    text_search = " ".join(part for part in (make, model) if part)

    all_vehicles: list[dict] = []
    async with AsyncSession(impersonate="chrome131") as client:
        page_number = 1
        while True:
            payload = _build_payload(text_search, page_number, MAX_PAGE_SIZE)
            resp = await client.post(api_url, json=payload, headers=headers)
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
            "city": dealer.city,
            "province": dealer.province,
            "dealer": dealer.name,
            "url": _build_vdp_url(dealer.base_url, v),
        })

    return {"total": total, "page": page, "per_page": per_page, "vehicles": vehicles}
