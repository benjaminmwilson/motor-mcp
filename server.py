#!/usr/bin/env python3
"""Motor MCP server — search vehicle inventory across multiple dealerships."""

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from datasources import DATASOURCES

SEARCH_SCHEMA = {
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
}

app = Server("motor-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=f"search_vehicles_{ds.short_name}",
            description=(
                f"Search {ds.long_name} inventory by make, model, and/or year range. "
                "Returns matching vehicles with price, odometer, trim, colour, VIN, "
                "and a direct listing URL. All parameters are optional but at least "
                "one must be provided."
            ),
            inputSchema=SEARCH_SCHEMA,
        )
        for ds in DATASOURCES.values()
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if not name.startswith("search_vehicles_"):
        raise ValueError(f"Unknown tool: {name}")
    short_name = name.removeprefix("search_vehicles_")
    if short_name not in DATASOURCES:
        raise ValueError(f"Unknown tool: {name}")
    ds = DATASOURCES[short_name]

    make = arguments.get("make")
    model = arguments.get("model")
    year_min = arguments.get("year_min")
    year_max = arguments.get("year_max")
    page = int(arguments.get("page", 1))
    per_page = min(int(arguments.get("per_page", 25)), 100)

    if not any([make, model, year_min, year_max]):
        return [TextContent(type="text", text="Provide at least one of: make, model, year_min, year_max.")]

    result = await ds.search(make, model, year_min, year_max, page, per_page)

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
