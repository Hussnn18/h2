"""Read and write config/*.yaml for the portal, keeping the plain-English comments.

Every save is checked with the same Pydantic models the engine uses, so the
portal can never write a file the engine would reject.
"""

import json
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from engine.rules import CONFIG_DIR, Offer, Rules

_yaml = YAML()  # round-trip mode: keeps comments, key order and [flow, style]
_yaml.preserve_quotes = True
_yaml.width = 4096  # never wrap long messages onto a second line
_yaml.indent(mapping=2, sequence=4, offset=2)


def load_raw(name: str, config_dir: Path = CONFIG_DIR) -> CommentedMap:
    return _yaml.load((Path(config_dir) / name).read_text(encoding="utf-8"))


def _plain(data: Any) -> Any:
    return json.loads(json.dumps(data))


def _write(path: Path, data: CommentedMap) -> None:
    buf = StringIO()
    _yaml.dump(data, buf)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(path)  # atomic: the bot never reads a half-written file


def save_rules(data: CommentedMap, config_dir: Path = CONFIG_DIR) -> Rules:
    rules = Rules.model_validate(_plain(data))  # raises pydantic.ValidationError
    _write(Path(config_dir) / "rules.yaml", data)
    return rules


def save_offers(data: CommentedMap, config_dir: Path = CONFIG_DIR) -> dict[str, Offer]:
    offers = {offer_id: Offer.model_validate(o) for offer_id, o in _plain(data)["offers"].items()}
    _write(Path(config_dir) / "offers.yaml", data)
    return offers


OFFER_KEY_ORDER = ["name", "enabled", "kind", "goal", "range", "min_order_inr", "cost_inr", "eligible_when",
                   "valid_hours", "fallback_message"]


def set_in_order(entry: CommentedMap, key: str, value: Any, order: list[str] = OFFER_KEY_ORDER) -> None:
    """Set a key, inserting a new one at its usual place instead of after the blank line that ends the entry."""
    if key in entry:
        entry[key] = value
        return
    rank = order.index(key)
    pos = sum(1 for k in entry if k in order and order.index(k) < rank)
    entry.insert(pos, key, value)


def flow_map(values: dict) -> CommentedMap:
    """A mapping written inline, like {has_cart: true}."""
    m = CommentedMap(values)
    m.fa.set_flow_style()
    return m


def flow_list(values: list) -> CommentedSeq:
    """A list written inline, like [10, 20]."""
    s = CommentedSeq(values)
    s.fa.set_flow_style()
    return s


def quoted(text: str) -> DoubleQuotedScalarString:
    """A string written in "double quotes", so emojis and colons stay safe."""
    return DoubleQuotedScalarString(text)
