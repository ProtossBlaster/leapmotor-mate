"""Import an untrusted legacy inventory as reference, never as executable code."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ValidationError

_MAX_BYTES = 1024 * 1024
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,95}")
_CMD_ID = re.compile(r"[0-9]{1,12}")


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    action: str
    cmd_id: str
    required_right: int | None
    requires_pin: bool
    evidence: str = field(default="legacy_inventory_unverified", init=False)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValidationError("Duplicate catalog field")
        obj[key] = value
    return obj


def load_reference_catalog(path: Path) -> tuple[CatalogEntry, ...]:
    if not isinstance(path, Path):
        raise ValidationError("Catalog path must be a Path")
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except OSError:
        raise ValidationError("Cannot read reference catalog") from None
    if len(raw) > _MAX_BYTES:
        raise ValidationError("Reference catalog exceeds size limit")
    try:
        doc = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError("Invalid reference catalog JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("registry"), list):
        raise ValidationError("Missing reference registry")
    if len(doc["registry"]) > 10000:
        raise ValidationError("Too many reference actions")
    entries, names = [], set()
    required = {"action", "cmd_id", "required_right", "requires_pin"}
    for row in doc["registry"]:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValidationError("Incomplete reference action")
        action, cmd_id = row["action"], row["cmd_id"]
        if not isinstance(action, str) or not _ACTION.fullmatch(action):
            raise ValidationError("Invalid reference action name")
        if action in names:
            raise ValidationError("Duplicate reference action")
        if not isinstance(cmd_id, str) or not _CMD_ID.fullmatch(cmd_id):
            raise ValidationError("Invalid reference command identifier")
        right = row["required_right"]
        if right is not None and (type(right) is not int or right <= 0):
            raise ValidationError("Invalid reference permission")
        if type(row["requires_pin"]) is not bool:
            raise ValidationError("Invalid reference PIN requirement")
        names.add(action)
        # Extra fields, including claimed evidence or executable payloads, are
        # deliberately not imported into a command execution registry.
        entries.append(CatalogEntry(action, cmd_id, right, row["requires_pin"]))
    return tuple(entries)

