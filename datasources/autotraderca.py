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

DEFAULT_POSTAL_CODE = "M5H2N2"  # Toronto financial district

# Decodes to: as24-search-funnel:vnrfbbBjI32Ol1Wka6uNHRp3EYn4dj
# The prefix is stable — it encodes the literal string "as24-search-funnel".
# On 401/403 we scrape the Next.js JS bundles to refresh it.
_AUTH_B64_PREFIX = "YXMyNC1zZWFyY2gtZnVubmVs"
_cached_auth = "Basic YXMyNC1zZWFyY2gtZnVubmVsOnZucmZiYkJqSTMyT2wxV2thNnVOSFJwM0VZbjRkag=="

_geocode_cache: dict[str, tuple[float, float, str]] = {}
_taxonomy_cache: dict[str, Optional[str]] = {}

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
        metadata={"description": "Canadian postal code for proximity sorting (e.g. 'V9C3M3'). Defaults to Toronto."},
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


async def _resolve_cat(make: Optional[str], model: Optional[str]) -> Optional[str]:
    """
    Resolve make/model names to a taxonomy identifier via getFreeTextTaxonomyV2.

    Returns:
      - "ma31gr200622"  (cat code) when make+model → used as cat=... in queryString
      - "31|||"         (mmmv string) when make-only → used as mmmv=... in queryString
      - None            when make is absent or lookup fails
    """
    if not make:
        return None

    cache_key = f"{make.lower()}|{(model or '').lower()}"
    if cache_key in _taxonomy_cache:
        return _taxonomy_cache[cache_key]

    search_term = make + (" " + model if model else "")
    try:
        async with AsyncSession(impersonate="chrome131") as session:
            resp = await session.post(
                GRAPHQL_URL,
                json={
                    "operationName": "ResolveCat",
                    "query": """
                    query ResolveCat($term: String!) {
                      getFreeTextTaxonomyV2(searchTerm: $term) {
                        items { cat displayName }
                      }
                    }
                    """,
                    "variables": {"term": search_term},
                },
                headers={
                    "Authorization": _cached_auth,
                    "x-culture": "en-CA",
                    "Content-Type": "application/json",
                },
            )
            items = (resp.json().get("data") or {}).get("getFreeTextTaxonomyV2", {}).get("items") or []
            if items:
                cat = items[0]["cat"]
                if model:
                    result: Optional[str] = cat
                else:
                    # make-only: extract the numeric make ID from the cat code
                    m = re.search(r"ma(\d+)", cat)
                    result = f"{m.group(1)}|||" if m else None
                _taxonomy_cache[cache_key] = result
                return result
    except Exception:
        pass

    _taxonomy_cache[cache_key] = None
    return None


_SEARCH_GQL = """
query SearchListings($qs: String!, $locale: Locale_) {
  search {
    listingsByQueryString(queryString: $qs, locale: $locale) {
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


def _build_qs(
    cat: Optional[str],
    year_min: Optional[int],
    year_max: Optional[int],
    lat: float,
    lon: float,
    postal_code: str,
    city: str,
    province_abbr: str,
    page: int,
    per_page: int,
) -> str:
    parts = []
    if cat:
        # mmmv strings contain "|" (e.g. "31|||"); cat codes do not (e.g. "ma31gr200622")
        parts.append(f"mmmv={cat}" if "|" in cat else f"cat={cat}")
    if year_min or year_max:
        lo = year_min or 1900
        hi = year_max or year_min
        parts.append(f"yr={lo}-{hi}")
    zip_str = quote(f"{postal_code} {city}, {province_abbr}", safe="")
    parts += [
        f"zip={zip_str}", f"lat={lat:.6f}", f"lon={lon:.6f}", "zipr=100",
        "offer=U", "cy=CA", "damaged_listing=exclude", "atype=C",
        "sort=standard", "desc=0", f"pg={page}", f"size={per_page}",
    ]
    return "&".join(parts)


async def _do_gql(session: AsyncSession, qs: str, auth: str):
    return await session.post(
        GRAPHQL_URL,
        json={
            "operationName": "SearchListings",
            "query": _SEARCH_GQL,
            "variables": {"qs": qs, "locale": "en_CA"},
        },
        headers={
            "Authorization": auth,
            "x-culture": "en-CA",
            "culture": "en-CA",
            "Content-Type": "application/json",
        },
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

    postal_code = (extras.postal_code or DEFAULT_POSTAL_CODE).upper().replace(" ", "")
    province_abbr = _PROVINCE_ABBR.get(postal_code[0] if postal_code else "M", "ON")

    # Geocode and taxonomy lookup run in parallel
    (lat, lon, city), cat = await asyncio.gather(
        _geocode(postal_code),
        _resolve_cat(make, model),
    )

    # Overfetch when year filters are active — T10 promoted listings bypass the server-side
    # yr= filter, so we request more and trim after client-side year filtering.
    fetch_size = min(per_page * 4, 100) if (year_min or year_max) else per_page
    qs = _build_qs(cat, year_min, year_max, lat, lon, postal_code, city, province_abbr, page, fetch_size)

    async with AsyncSession(impersonate="chrome131") as session:
        resp = await _do_gql(session, qs, _cached_auth)
        if resp.status_code in (401, 403):
            _cached_auth = await _refresh_auth()
            resp = await _do_gql(session, qs, _cached_auth)
        resp.raise_for_status()

    data = resp.json()
    if "errors" in data and not data.get("data"):
        raise ValueError(f"Autotrader GraphQL error: {data['errors'][0]['message']}")

    r = data["data"]["search"]["listingsByQueryString"]
    meta = r["metadata"]

    vehicles = []
    for listing in r["listings"]:
        d = listing.get("details") or {}
        v = d.get("vehicle") or {}
        clf = v.get("classification") or {}
        cond = v.get("condition") or {}
        engine = v.get("engine") or {}

        year = clf.get("modelYear")
        # Client-side year filter: T10 promoted listings bypass the server-side yr= parameter
        if year_min and year and year < year_min:
            continue
        if year_max and year and year > year_max:
            continue

        odometer_raw = (cond.get("mileageInKm") or {}).get("raw")
        price_raw = (((d.get("prices") or {}).get("public") or {}).get("amountInEUR") or {}).get("raw")
        fuel = (((v.get("fuels") or {}).get("primary") or {}).get("type") or {}).get("formatted")
        loc = d.get("location") or {}

        vehicles.append({
            "year": year,
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
        "vehicles": vehicles[:per_page],
    }
