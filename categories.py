"""Conservative, teacher-specific routing shared by sync and archive preview."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = Path(__file__).with_name("categories.json")


@dataclass(frozen=True)
class Routing:
    iyun_series: tuple[str, ...] = ()
    overrides: tuple[tuple[str, str], ...] = ()

    def category(self, shiur_id: str, title: str) -> str:
        override = dict(self.overrides).get(shiur_id)
        if override:
            return override
        # Require an explicitly numbered series at the start of the title.
        # Merely mentioning Shabbos (e.g. in a parsha title) is not evidence.
        for series in self.iyun_series:
            if re.match(r"^\s*" + re.escape(series) + r"\s*#\s*\d+(?=$|[^\w]|_)", title, re.IGNORECASE):
                return "Iyun"
        return "Other"


def load_routing(teacher_id: str, path: Path = DEFAULT_CONFIG) -> Routing:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Category configuration must map teacher IDs to rules")
    for teacher, rule in data.items():
        if not teacher.isdigit() or not isinstance(rule, dict):
            raise ValueError("Category rules must be objects keyed by numeric teacher IDs")
        if set(rule) - {"iyun_series", "overrides"}:
            raise ValueError(f"Unknown category setting for teacher {teacher}")
        series = rule.get("iyun_series", [])
        overrides = rule.get("overrides", {})
        if not isinstance(series, list) or any(not isinstance(s, str) or not s.strip() for s in series):
            raise ValueError("iyun_series must be a list of nonempty series names")
        if not isinstance(overrides, dict) or any(
            not key.isdigit() or value not in ("Iyun", "Other") for key, value in overrides.items()
        ):
            raise ValueError("overrides must map numeric shiur IDs to Iyun or Other")
    rule = data.get(teacher_id, {})
    return Routing(tuple(s.strip() for s in rule.get("iyun_series", [])), tuple(rule.get("overrides", {}).items()))
