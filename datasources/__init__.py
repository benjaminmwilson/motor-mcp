import dataclasses
import typing
from dataclasses import dataclass
from typing import Callable

from datasources import autotraderca, galmo
from datasources.autotraderca import AutotraderExtras
from datasources.galmo import GalmoExtras

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def extras_schema(extras_type: type) -> dict:
    """Derive a JSON Schema properties dict from a dataclass extras type."""
    hints = typing.get_type_hints(extras_type)
    result = {}
    for f in dataclasses.fields(extras_type):
        py_type = hints.get(f.name, str)
        if typing.get_origin(py_type) is typing.Union:
            args = [a for a in typing.get_args(py_type) if a is not type(None)]
            py_type = args[0] if args else str
        result[f.name] = {
            "type": _JSON_TYPES.get(py_type, "string"),
            "description": f.metadata.get("description", ""),
        }
    return result


@dataclass
class Datasource:
    short_name: str
    long_name: str
    search: Callable
    extras_type: type


DATASOURCES: dict[str, Datasource] = {
    "autotraderca": Datasource(
        short_name="autotraderca",
        long_name="Autotrader Canada",
        search=autotraderca.search_inventory,
        extras_type=AutotraderExtras,
    ),
    "galmo": Datasource(
        short_name="galmo",
        long_name="Galaxy Motors",
        search=galmo.search_inventory,
        extras_type=GalmoExtras,
    ),
}
