"""Field paths like `seller.tax_id` or `line_items[2].unit_price` into the extraction models.

Used by reextract_fields to check requested paths and to coerce the model's answers into
the right type (date, float, str) for each path.
"""

import re
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from docagent_schemas import Field

_SEGMENT = re.compile(r"^([a-z_]+)(?:\[(\d+)\])?$")


class UnknownFieldPath(KeyError):
    pass


def value_type(model: type[BaseModel], path: str) -> Any:
    """The `value` annotation (e.g. `float | None`) of the `Field` at `path`."""
    current: type[BaseModel] = model
    parts = path.split(".")
    for n, part in enumerate(parts):
        m = _SEGMENT.match(part)
        if m is None or m.group(1) not in current.model_fields:
            raise UnknownFieldPath(path)
        name, index = m.groups()
        ann: Any = current.model_fields[name].annotation
        if get_origin(ann) is list:
            if index is None:
                raise UnknownFieldPath(f"{path}: {name} is a list, give an index like {name}[0]")
            ann = get_args(ann)[0]
        elif index is not None:
            raise UnknownFieldPath(f"{path}: {name} is not a list")
        if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
            raise UnknownFieldPath(path)
        if n == len(parts) - 1:
            if not issubclass(ann, Field):
                raise UnknownFieldPath(f"{path} is a group of fields, not a single field")
            return ann.model_fields["value"].annotation
        if issubclass(ann, Field):
            raise UnknownFieldPath(path)
        current = ann
    raise UnknownFieldPath(path)


def describe_type(annotation: Any) -> str:
    args = {a.__name__ for a in get_args(annotation) if a is not type(None)}
    if "date" in args:
        return "date as YYYY-MM-DD"
    if "float" in args:
        return "plain number"
    return "text"
