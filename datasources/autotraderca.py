"""Autotrader Canada (autotraderca) datasource — vehicle listings via GraphQL API."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

BASE_URL = "https://www.autotrader.ca"
GRAPHQL_URL = f"{BASE_URL}/listing-search-api/graphql"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"

DEFAULT_POSTAL_CODE = "M5H2N2"  # Toronto financial district

# Decodes to: as24-search-funnel:vnrfbbBjI32Ol1Wka6uNHRp3EYn4dj
# The prefix is stable — it encodes the literal string "as24-search-funnel".
# On 401/403 we scrape the Next.js JS bundles to refresh it.
_AUTH_B64_PREFIX = "YXMyNC1zZWFyY2gtZnVubmVs"
_cached_auth = "Basic YXMyNC1zZWFyY2gtZnVubmVsOnZucmZiYkJqSTMyT2wxV2thNnVOSFJwM0VZbjRkag=="

_geocode_cache: dict[str, tuple[float, float, str]] = {}
_reverse_geocode_cache: dict[str, tuple[str, str]] = {}
# Keyed by "make|model" (lowercased) → (make_id, model_id)
_id_cache: dict[str, tuple[Optional[int], Optional[int]]] = {}

_PROVINCE_ABBR: dict[str, str] = {
    "A": "NL", "B": "NS", "C": "PE", "E": "NB",
    "G": "QC", "H": "QC", "J": "QC",
    "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON",
    "R": "MB", "S": "SK", "T": "AB", "V": "BC",
    "X": "NT", "Y": "YT",
}

_PROVINCE_DEFAULTS: dict[str, tuple[float, float, str]] = {
    "ON": (43.6488, -79.3844, "Toronto"),
    "BC": (49.2827, -123.1207, "Vancouver"),
    "AB": (51.0447, -114.0719, "Calgary"),
    "QC": (45.5017, -73.5673, "Montreal"),
    "MB": (49.8951, -97.1384, "Winnipeg"),
    "SK": (52.1332, -106.6700, "Saskatoon"),
    "NS": (44.6488, -63.5752, "Halifax"),
    "NB": (45.9636, -66.6431, "Fredericton"),
    "NL": (47.5615, -52.7126, "St. John's"),
    "PE": (46.2382, -63.1311, "Charlottetown"),
    "NT": (62.4540, -114.3718, "Yellowknife"),
    "YT": (60.7212, -135.0568, "Whitehorse"),
}


@dataclass
class AutotraderExtras:
    postal_code: str = field(
        default=DEFAULT_POSTAL_CODE,
        metadata={"description": "Canadian postal code for proximity sorting (e.g. 'V9C3M3'). Defaults to Toronto. Ignored if latitude/longitude are provided."},
    )
    latitude: Optional[float] = field(
        default=None,
        metadata={"description": "Latitude for proximity sorting, used instead of postal_code (e.g. 49.2827). Must be given together with longitude."},
    )
    longitude: Optional[float] = field(
        default=None,
        metadata={"description": "Longitude for proximity sorting, used instead of postal_code (e.g. -123.1207). Must be given together with latitude."},
    )
    odometer_max_km: Optional[int] = field(
        default=None,
        metadata={"description": "Maximum odometer reading in km (e.g. 150000). Optional."},
    )


async def _refresh_auth() -> str:
    """Re-extract the Basic auth token from Autotrader's Next.js JS bundles on 401/403."""
    global _cached_auth
    try:
        async with AsyncSession(impersonate="chrome131") as session:
            resp = await session.get(BASE_URL + "/")
            srcs = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', resp.text)
            for src in srcs[:20]:
                try:
                    chunk = await session.get(BASE_URL + src)
                    m = re.search(rf"Basic ({_AUTH_B64_PREFIX}[A-Za-z0-9+/=]+)", chunk.text)
                    if m:
                        _cached_auth = "Basic " + m.group(1)
                        return _cached_auth
                except Exception:
                    continue
    except Exception:
        pass
    return _cached_auth


async def _geocode(postal_code: str) -> tuple[float, float, str]:
    """Convert a Canadian postal code to (lat, lon, city) via Nominatim."""
    key = postal_code.upper().replace(" ", "")[:6]
    if key in _geocode_cache:
        return _geocode_cache[key]

    province = _PROVINCE_ABBR.get(key[0] if key else "M", "ON")
    default = _PROVINCE_DEFAULTS.get(province, _PROVINCE_DEFAULTS["ON"])

    try:
        async with AsyncSession(impersonate="chrome131") as session:
            resp = await session.get(
                NOMINATIM_URL,
                params={"q": f"{key} Canada", "format": "json", "limit": "1", "addressdetails": "1"},
                headers={"User-Agent": "motor-mcp/1.0"},
            )
            results = resp.json()
            if results:
                addr = results[0].get("address", {})
                city = (addr.get("city") or addr.get("town") or addr.get("village") or default[2])
                result = (float(results[0]["lat"]), float(results[0]["lon"]), city)
                _geocode_cache[key] = result
                return result
    except Exception:
        pass

    _geocode_cache[key] = default
    return default


async def _reverse_geocode(lat: float, lon: float) -> tuple[str, str]:
    """Convert (lat, lon) to (postal_code, city) via Nominatim reverse geocoding."""
    key = f"{lat:.4f},{lon:.4f}"
    if key in _reverse_geocode_cache:
        return _reverse_geocode_cache[key]

    result = ("", "")
    try:
        async with AsyncSession(impersonate="chrome131") as session:
            resp = await session.get(
                NOMINATIM_REVERSE_URL,
                params={"lat": str(lat), "lon": str(lon), "format": "json", "addressdetails": "1"},
                headers={"User-Agent": "motor-mcp/1.0"},
            )
            addr = (resp.json() or {}).get("address", {})
            postal_code = (addr.get("postcode") or "").upper().replace(" ", "")
            city = addr.get("city") or addr.get("town") or addr.get("village") or ""
            result = (postal_code, city)
    except Exception:
        pass

    _reverse_geocode_cache[key] = result
    return result


_SAMPLE_GQL = """
query SampleListing($qs: String!, $locale: Locale_) {
  search {
    listingsByQueryString(queryString: $qs, locale: $locale) {
      listings { details { vehicle { classification { model { raw } } } } }
    }
  }
}
"""


async def _resolve_ids(make: Optional[str], model: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """
    Resolve make/model names to structured API integer IDs.

    Returns (make_id, model_id) where:
      make_id:  classification.make int (e.g. 31 for Honda)
      model_id: classification.model int (e.g. 1775 for Civic), None for make-only searches
      (None, None) when make is absent or lookup fails
    """
    if not make:
        return None, None

    cache_key = f"{make.lower()}|{(model or '').lower()}"
    if cache_key in _id_cache:
        return _id_cache[cache_key]

    search_term = make + (" " + model if model else "")
    try:
        async with AsyncSession(impersonate="chrome131") as session:
            # Step 1: resolve text → cat code via taxonomy
            resp = await session.post(
                GRAPHQL_URL,
                json={
                    "operationName": "ResolveCat",
                    "query": """
                    query ResolveCat($term: String!) {
                      getFreeTextTaxonomyV2(searchTerm: $term) {
                        items { cat }
                      }
                    }
                    """,
                    "variables": {"term": search_term},
                },
                headers={"Authorization": _cached_auth, "x-culture": "en-CA", "Content-Type": "application/json"},
            )
            items = (resp.json().get("data") or {}).get("getFreeTextTaxonomyV2", {}).get("items") or []
            if not items:
                _id_cache[cache_key] = (None, None)
                return None, None

            cat = items[0]["cat"]
            m = re.search(r"ma(\d+)", cat)
            if not m:
                _id_cache[cache_key] = (None, None)
                return None, None
            make_id = int(m.group(1))

            if not model:
                _id_cache[cache_key] = (make_id, None)
                return make_id, None

            # Step 2: fetch one sample listing to get the structured API's model.raw ID.
            # The cat code from getFreeTextTaxonomyV2 uses Autotrader CA's internal group ID
            # (e.g. gr200622 for Civic) which does NOT map to the structured listings API's
            # classification.model field. We resolve it by fetching one listing.
            zip_str = quote("M5H2N2 Toronto, ON", safe="")
            qs = f"cat={cat}&zip={zip_str}&lat=43.6488&lon=-79.3844&zipr=100&cy=CA&atype=C&pg=1&size=1"
            resp2 = await session.post(
                GRAPHQL_URL,
                json={
                    "operationName": "SampleListing",
                    "query": _SAMPLE_GQL,
                    "variables": {"qs": qs, "locale": "en_CA"},
                },
                headers={"Authorization": _cached_auth, "x-culture": "en-CA", "Content-Type": "application/json"},
            )
            listings = (
                (resp2.json().get("data") or {})
                .get("search", {})
                .get("listingsByQueryString", {})
                .get("listings") or []
            )
            if listings:
                model_raw = (
                    (listings[0]["details"]["vehicle"]["classification"].get("model") or {}).get("raw")
                )
                model_id: Optional[int] = int(model_raw) if model_raw is not None else None
            else:
                model_id = None

            _id_cache[cache_key] = (make_id, model_id)
            return make_id, model_id
    except Exception:
        pass

    _id_cache[cache_key] = (None, None)
    return None, None


_SEARCH_GQL = """
query StructuredListings($vehicle: Vehicle_, $location: Location_, $metadata: Metadata_, $locale: Locale_) {
  search {
    listings(vehicle: $vehicle, location: $location, metadata: $metadata, locale: $locale) {
      metadata { currentPage pageSize totalItems totalPages }
      listings {
        id
        details {
          webPage
          vehicle {
            classification {
              make { formatted }
              model { formatted }
              modelVersionInput
              modelYear
            }
            condition { mileageInKm { raw } }
            bodyColor { formatted }
            bodyType { formatted }
            fuels { primary { type { formatted } } }
            engine { transmissionType { formatted } driveTrain { formatted } }
            usageState
          }
          prices { public { amountInEUR { raw } } }
          location { city distanceToSearchLocationInKm }
        }
      }
    }
  }
}
"""

# amountInEUR is the AS24 internal price field name; for Canadian listings it returns CAD.


async def _do_structured(session: AsyncSession, vars_: dict, auth: str):
    return await session.post(
        GRAPHQL_URL,
        json={"operationName": "StructuredListings", "query": _SEARCH_GQL, "variables": vars_},
        headers={"Authorization": auth, "x-culture": "en-CA", "Content-Type": "application/json"},
    )


async def search_inventory(
    make: Optional[str],
    model: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    page: int = 1,
    per_page: int = 25,
    extras: AutotraderExtras = AutotraderExtras(),
) -> dict:
    global _cached_auth

    if (extras.latitude is None) != (extras.longitude is None):
        raise ValueError("latitude and longitude must be provided together")

    if extras.latitude is not None and extras.longitude is not None:
        lat, lon = extras.latitude, extras.longitude
        # Reverse-geocode and ID resolution run in parallel
        (postal_code, city), (make_id, model_id) = await asyncio.gather(
            _reverse_geocode(lat, lon),
            _resolve_ids(make, model),
        )
        postal_code = postal_code or DEFAULT_POSTAL_CODE
    else:
        postal_code = (extras.postal_code or DEFAULT_POSTAL_CODE).upper().replace(" ", "")
        # Geocode and ID resolution run in parallel
        (lat, lon, city), (make_id, model_id) = await asyncio.gather(
            _geocode(postal_code),
            _resolve_ids(make, model),
        )

    province_abbr = _PROVINCE_ABBR.get(postal_code[0] if postal_code else "M", "ON")

    # Build structured Vehicle_ input
    classification: dict = {}
    if make_id is not None:
        classification["make"] = make_id
    if model_id is not None:
        classification["model"] = model_id

    vehicle: dict = {
        "vehicleType": [], "bodyColor": [], "bodyType": [], "equipment": [],
        "fuel": [], "transmissionType": [], "interiorColor": [],
        "offerType": [], "paintType": [], "upholstery": [],
        "damagedListing": "Exclude",
    }
    if classification:
        vehicle["classification"] = classification
    if year_min or year_max:
        vehicle["modelYear"] = {"from": year_min or 1900, "to": year_max or 9999}
    if extras.odometer_max_km:
        vehicle["mileageInKm"] = {"to": extras.odometer_max_km}

    vars_ = {
        "locale": "en_CA",
        "vehicle": vehicle,
        "location": {
            "country": "Canada",
            "zip": postal_code,
            "position": {"latitude": lat, "longitude": lon},
            "radius": 100,
        },
        "metadata": {
            "sort": {"field": "Standard", "order": "Asc"},
            "page": page,
            "size": per_page,
        },
    }

    async with AsyncSession(impersonate="chrome131") as session:
        resp = await _do_structured(session, vars_, _cached_auth)
        if resp.status_code in (401, 403):
            _cached_auth = await _refresh_auth()
            resp = await _do_structured(session, vars_, _cached_auth)
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data and not data.get("data"):
        raise ValueError(f"Autotrader GraphQL error: {data['errors'][0]['message']}")

    r = data["data"]["search"]["listings"]
    meta = r["metadata"]

    vehicles = []
    for listing in r["listings"]:
        d = listing.get("details") or {}
        v = d.get("vehicle") or {}
        clf = v.get("classification") or {}
        cond = v.get("condition") or {}
        engine = v.get("engine") or {}

        odometer_raw = (cond.get("mileageInKm") or {}).get("raw")
        price_raw = (((d.get("prices") or {}).get("public") or {}).get("amountInEUR") or {}).get("raw")
        fuel = (((v.get("fuels") or {}).get("primary") or {}).get("type") or {}).get("formatted")
        loc = d.get("location") or {}

        vehicles.append({
            "year": clf.get("modelYear"),
            "make": (clf.get("make") or {}).get("formatted"),
            "model": (clf.get("model") or {}).get("formatted"),
            "trim": clf.get("modelVersionInput"),
            "condition": "Used" if v.get("usageState") == "U" else "New",
            "body_style": (v.get("bodyType") or {}).get("formatted"),
            "drivetrain": (engine.get("driveTrain") or {}).get("formatted"),
            "transmission": (engine.get("transmissionType") or {}).get("formatted"),
            "fuel_type": fuel,
            "engine": None,
            "exterior_colour": (v.get("bodyColor") or {}).get("formatted"),
            "interior_colour": None,
            "odometer_km": int(odometer_raw) if odometer_raw is not None else None,
            "price_cad": price_raw,
            "msrp_cad": None,
            "stock": None,
            "vin": None,
            "days_on_lot": None,
            "city": loc.get("city"),
            "province": province_abbr if loc.get("city") else None,
            "url": d.get("webPage"),
        })

    return {
        "total": meta["totalItems"],
        "page": meta["currentPage"],
        "per_page": per_page,
        "vehicles": vehicles,
    }
