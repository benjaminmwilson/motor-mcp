from dataclasses import dataclass
from typing import Callable

from datasources import galmo


@dataclass
class Datasource:
    short_name: str
    long_name: str
    search: Callable


DATASOURCES: dict[str, Datasource] = {
    "galmo": Datasource(
        short_name="galmo",
        long_name="Galaxy Motors",
        search=galmo.search_inventory,
    ),
}
