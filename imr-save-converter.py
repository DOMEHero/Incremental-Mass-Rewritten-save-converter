#!/usr/bin/env python3
"""
Convert an Incremental Mass Rewritten v0.8 save into a v0.7.1.6-compatible save.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any


def b64decode_save(text: str) -> dict[str, Any]:
    s = "".join(text.strip().split())
    padding = (-len(s)) % 4
    s += "=" * padding

    try:
        raw = base64.b64decode(s, validate=False)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to decode save: {exc}") from exc


def b64encode_save(data: dict[str, Any]) -> str:
    # Browser btoa(JSON.stringify(player)) produces compact ASCII-compatible JSON.
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def expand_range_list(value: Any) -> list[int]:
    """
    Converts v0.8 range-compressed lists into v0.7-style flat numeric lists.

    Examples:
        [[1, 243], 246, 249] -> [1, 2, ..., 243, 246, 249]
        [[1, 31]] -> [1, 2, ..., 31]
    """
    result: list[int] = []

    if not isinstance(value, list):
        return result

    for item in value:
        if isinstance(item, list) and len(item) == 2:
            try:
                start = int(item[0])
                end = int(item[1])
            except Exception:
                continue

            step = 1 if end >= start else -1
            result.extend(range(start, end + step, step))

        else:
            try:
                result.append(int(item))
            except Exception:
                continue

    # Preserve order while removing duplicates.
    seen: set[int] = set()
    out: list[int] = []
    for x in result:
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


def as_bool_list(value: Any, length: int, default: bool = False) -> list[bool]:
    if not isinstance(value, list):
        value = []

    out: list[bool] = []
    for i in range(length):
        if i < len(value):
            out.append(bool(value[i]))
        else:
            out.append(default)
    return out


def ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def ensure_list(parent: dict[str, Any], key: str) -> list[Any]:
    if not isinstance(parent.get(key), list):
        parent[key] = []
    return parent[key]


def patch_save(data: dict[str, Any], *, drop_muonic: bool, pre_infinity: bool) -> dict[str, Any]:
    # ---- atom: v0.8 compressed ranges -> v0.7 flat lists ----
    atom = ensure_dict(data, "atom")

    atom["elements"] = expand_range_list(atom.get("elements", []))

    if drop_muonic:
        atom["muonic_el"] = []
    else:
        atom["muonic_el"] = expand_range_list(atom.get("muonic_el", []))

    # v0.7 expects elemTier to be a 2-entry list.
    elem_tier = atom.get("elemTier", [1, 1])
    if isinstance(elem_tier, int):
        atom["elemTier"] = [elem_tier, 1]
    elif isinstance(elem_tier, list):
        atom["elemTier"] = (elem_tier + [1, 1])[:2]
    else:
        atom["elemTier"] = [1, 1]

    atom["elemLayer"] = int(atom.get("elemLayer", 0) or 0)

    # ---- options: v0.7.1.6 has only two nav hiders ----
    options = ensure_dict(data, "options")
    options["nav_hide"] = as_bool_list(options.get("nav_hide", []), 2, False)

    pins = options.get("pins", [])
    if isinstance(pins, list):
        options["pins"] = [p for p in pins if p != "inf-core"]
    else:
        options["pins"] = []

    # ---- transient states: avoid importing directly into broken active modes ----
    chal = ensure_dict(data, "chal")
    chal["active"] = 0
    chal["choosed"] = 0

    qu = ensure_dict(data, "qu")
    qc = ensure_dict(qu, "qc")
    qc["active"] = False

    rip = ensure_dict(qu, "rip")
    rip["active"] = False

    dark = ensure_dict(data, "dark")
    dark_run = ensure_dict(dark, "run")
    dark_run["active"] = False

    # ---- Infinity: preserve progress, but normalize shape ----
    inf = ensure_dict(data, "inf")
    inf.setdefault("core", [])
    inf.setdefault("inv", [])
    inf.setdefault("pre_theorem", [])
    inf.setdefault("upg", [])
    inf.setdefault("fragment", {})
    inf.setdefault("pt_choosed", -1)
    inf.setdefault("cs_amount", "0")
    inf.setdefault("cs_double", ["0", "0"])
    inf.setdefault("dim_mass", "0")

    if not isinstance(inf["core"], list):
        inf["core"] = []
    if not isinstance(inf["inv"], list):
        inf["inv"] = []
    if not isinstance(inf["pre_theorem"], list):
        inf["pre_theorem"] = []
    if not isinstance(inf["upg"], list):
        inf["upg"] = []
    if not isinstance(inf["fragment"], dict):
        inf["fragment"] = {}

    # Common v0.7 Infinity core fragment keys.
    for key in ("mass", "bh", "atom", "proto", "time"):
        inf["fragment"].setdefault(key, "0")

    # Make theorem objects v0.7-friendly: level/power should be string-like decimal values.
    for arr_name in ("core", "inv"):
        arr = inf.get(arr_name, [])
        for item in arr:
            if isinstance(item, dict):
                item.setdefault("type", "mass")
                item.setdefault("star", [False] * 8)
                item["level"] = str(item.get("level", "1"))
                item["power"] = str(item.get("power", "1"))

    for item in inf.get("pre_theorem", []):
        if isinstance(item, dict):
            item.setdefault("type", "mass")
            item.setdefault("star_c", [0] * 8)
            item.setdefault("min_pow", "1")
            item["power_m"] = item.get("power_m", 1)

    # Optional: stop the save from immediately entering the Infinity popup on load.
    if pre_infinity:
        data["mass"] = "ee307"
        inf["reached"] = False

    # ---- remove obviously v0.8 UI-only cruft if present ----
    # Do not remove dark.c16 or dark.exotic_atom by default; v0.7.1.6 code references them.
    return data


def convert_file(input_path: Path, output_path: Path, *, drop_muonic: bool, pre_infinity: bool) -> None:
    text = input_path.read_text(encoding="utf-8")
    data = b64decode_save(text)
    patched = patch_save(data, drop_muonic=drop_muonic, pre_infinity=pre_infinity)
    output_path.write_text(b64encode_save(patched), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="v0.8 save .txt file")
    parser.add_argument("output", type=Path, help="converted v0.7.1.6 save .txt file")
    parser.add_argument(
        "--drop-muonic",
        action="store_true",
        help="remove atom.muonic_el entirely; use if Infinity reset still crashes on MUONIC_ELEM.upgs[...].cs",
    )
    parser.add_argument(
        "--pre-infinity",
        action="store_true",
        help="set mass below Infinity trigger to avoid the immediate Infinity popup",
    )
    args = parser.parse_args()

    try:
        convert_file(
            args.input,
            args.output,
            drop_muonic=args.drop_muonic,
            pre_infinity=args.pre_infinity,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote converted save to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())