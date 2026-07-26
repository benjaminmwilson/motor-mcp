#!/usr/bin/env python3
"""Galaxy Motors MCP server — search inventory by make, model, and year range."""

import asyncio
from typing import Optional
from urllib.parse import quote

from curl_cffi.requests import AsyncSession
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

INVENTORY_ID = "2767"
PROXY_URL = "https://www.galaxymotors.net/wp-content/plugins/convertus-vms/include/php/ajax-vehicles.php"
VMS_API_BASE = "https://vms.prod.convertus.rocks/api/filtering/"

API_HEADERS = {
    "Referer": "https://www.galaxymotors.net/vehicles/",
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
    # Build the VMS API endpoint that the WP PHP proxy will forward to
    params = [f"cp={INVENTORY_ID}", "ln=en"]
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
) -> dict:
    url = build_api_url(make, model, year_min, year_max, page, per_page)
    async with AsyncSession(impersonate="chrome131") as client:
        # Visit SRP first to pick up session cookies before the AJAX call
        await client.get("https://www.galaxymotors.net/vehicles/")
        resp = await client.get(url, headers=API_HEADERS)
        resp.raise_for_status()

    data = resp.json()
    total = data.get("summary", {}).get("total_vehicles", 0)
    results = data.get("results", [])

    vehicles = []
    for v in results:
        vehicles.append({
            "year": v.get("year"),
            "make": v.get("make"),
            "model": v.get("model"),
            "trim": v.get("trim"),
            "condition": v.get("sale_class"),
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
            "url": v.get("vdp_url"),
        })

    return {"total": total, "page": page, "per_page": per_page, "vehicles": vehicles}


app = Server("galaxy-motors")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_vehicles",
            description=(
                "Search Galaxy Motors inventory by make, model, and/or year range. "
                "Returns matching vehicles with price, odometer, trim, colour, VIN, "
                "and a direct listing URL. All parameters are optional but at least "
                "one must be provided."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "make": {
                        "type": "string",
                        "description": "Vehicle make, e.g. 'Toyota', 'Honda', 'Ford', 'Chevrolet'",
                    },
                    "model": {
                        "type": "string",
                        "description": "Vehicle model, e.g. 'Camry', 'Civic', 'F-150', 'Silverado'",
                    },
                    "year_min": {
                        "type": "integer",
                        "description": "Earliest model year to include (inclusive), e.g. 2018",
                    },
                    "year_max": {
                        "type": "integer",
                        "description": "Latest model year to include (inclusive), e.g. 2022",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number for pagination (default: 1)",
                        "default": 1,
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Results per page, max 100 (default: 25)",
                        "default": 25,
                    },
                },
                "additionalProperties": False,
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "search_vehicles":
        raise ValueError(f"Unknown tool: {name}")

    make = arguments.get("make")
    model = arguments.get("model")
    year_min = arguments.get("year_min")
    year_max = arguments.get("year_max")
    page = int(arguments.get("page", 1))
    per_page = min(int(arguments.get("per_page", 25)), 100)

    if not any([make, model, year_min, year_max]):
        return [TextContent(type="text", text="Provide at least one of: make, model, year_min, year_max.")]

    result = await search_inventory(make, model, year_min, year_max, page, per_page)

    lines = [
        f"Total matching: {result['total']}  |  Page {result['page']}  |  Showing {len(result['vehicles'])} vehicles",
        "",
    ]
    for v in result["vehicles"]:
        price = f"${v['price_cad']:,}" if v["price_cad"] else "N/A"
        odo = f"{v['odometer_km']:,} km" if v["odometer_km"] else "N/A"
        lines.append(
            f"{v['year']} {v['make']} {v['model']} {v['trim'] or ''}\n"
            f"  Condition: {v['condition']} | {v['exterior_colour']} | {odo} | {price}\n"
            f"  {v['drivetrain']} | {v['transmission']} | {v['fuel_type']}\n"
            f"  Stock: {v['stock']} | VIN: {v['vin']} | {v['days_on_lot']} days on lot\n"
            f"  {v['url']}\n"
        )

    if not result["vehicles"]:
        lines.append("No vehicles found for the given criteria.")

    return [TextContent(type="text", text="\n".join(lines))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
