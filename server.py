#!/usr/bin/env python3
"""Motor MCP server — search vehicle inventory across multiple dealerships."""

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from datasources import DATASOURCES, extras_schema

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

_BASE_FIELDS = {"make", "model", "year_min", "year_max", "page", "per_page"}


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    for ds in DATASOURCES.values():
        ds_extras = extras_schema(ds.extras_type)
        if ds_extras:
            schema = {
                **SEARCH_SCHEMA,
                "properties": {**SEARCH_SCHEMA["properties"], **ds_extras},
            }
        else:
            schema = SEARCH_SCHEMA
        tools.append(Tool(
            name=f"search_vehicles_{ds.short_name}",
            description=(
                f"Search {ds.long_name} inventory by make, model, and/or year range. "
                "Returns matching vehicles with price, odometer, trim, colour, VIN, "
                "and a direct listing URL. All parameters are optional but at least "
                "one must be provided."
            ),
            inputSchema=schema,
        ))
    return tools


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

    raw_extras = {k: v for k, v in arguments.items() if k not in _BASE_FIELDS}
    extras = ds.extras_type(**raw_extras)

    result = await ds.search(make, model, year_min, year_max, page, per_page, extras=extras)

    lines = [
        f"Total matching: {result['total']}  |  Page {result['page']}  |  Showing {len(result['vehicles'])} vehicles",
        "",
    ]
    for v in result["vehicles"]:
        price = f"${v['price_cad']:,}" if v.get("price_cad") else "N/A"
        odo = f"{v['odometer_km']:,} km" if v.get("odometer_km") else "N/A"

        detail_parts = [v.get("condition"), v.get("exterior_colour"), odo, price]
        detail = " | ".join(str(x) for x in detail_parts if x is not None)

        drivetrain_parts = [v.get("drivetrain"), v.get("transmission"), v.get("fuel_type")]
        drivetrain_line = " | ".join(str(x) for x in drivetrain_parts if x is not None)

        entry = f"{v['year']} {v['make']} {v['model']} {v.get('trim') or ''}\n"
        entry += f"  {detail}\n"
        if drivetrain_line:
            entry += f"  {drivetrain_line}\n"
        if v.get("stock"):
            entry += f"  Stock: {v['stock']} | VIN: {v.get('vin', 'N/A')} | {v.get('days_on_lot', 'N/A')} days on lot\n"
        if v.get("city"):
            entry += f"  {v['city']}, {v.get('province', '')}\n"
        entry += f"  {v['url']}\n"
        lines.append(entry)

    if not result["vehicles"]:
        lines.append("No vehicles found for the given criteria.")

    return [TextContent(type="text", text="\n".join(lines))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
