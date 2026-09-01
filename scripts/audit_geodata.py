#!/usr/bin/env python3
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from filter_geodat import read_varint, skip_field


DOMAIN_TYPE = {
    0: "plain",
    1: "regexp",
    2: "domain",
    3: "full",
}


def parse_geosite(path):
    data = Path(path).read_bytes()
    result = {}
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field = key >> 3
        wire = key & 7
        if field != 1 or wire != 2:
            pos = skip_field(data, pos, wire)
            continue
        length, pos = read_varint(data, pos)
        end = pos + length
        code, entries = parse_geosite_entry(data[pos:end])
        result[code] = entries
        pos = end
    return result


def parse_geosite_entry(data):
    code = None
    entries = []
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field = key >> 3
        wire = key & 7
        if field == 1 and wire == 2:
            length, pos = read_varint(data, pos)
            code = data[pos : pos + length].decode("utf-8").lower()
            pos += length
        elif field == 2 and wire == 2:
            length, pos = read_varint(data, pos)
            entries.append(parse_domain(data[pos : pos + length]))
            pos += length
        else:
            pos = skip_field(data, pos, wire)
    return code, entries


def parse_domain(data):
    typ = 0
    value = ""
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field = key >> 3
        wire = key & 7
        if field == 1 and wire == 0:
            typ, pos = read_varint(data, pos)
        elif field == 2 and wire == 2:
            length, pos = read_varint(data, pos)
            value = data[pos : pos + length].decode("utf-8").lower()
            pos += length
        else:
            pos = skip_field(data, pos, wire)
    return {"type": DOMAIN_TYPE.get(typ, str(typ)), "value": value}


def key(entry):
    return f"{entry['type']}:{entry['value']}"


def looks_ru_domain(entry):
    value = entry["value"].lower()
    if entry["type"] in {"domain", "full", "plain"}:
        return value.endswith(".ru") or value.endswith(".su") or value.endswith(".xn--p1ai") or value in {"ru", "su", "xn--p1ai"}
    if entry["type"] == "regexp":
        return "\\.ru" in value or "\\.su" in value or "xn--p1ai" in value
    return False


def summarize_overlap(groups, left, right):
    left_keys = {key(e): e for e in groups.get(left, [])}
    right_keys = {key(e): e for e in groups.get(right, [])}
    overlap = sorted(set(left_keys) & set(right_keys))
    return {
        "left": left,
        "right": right,
        "count": len(overlap),
        "examples": overlap[:20],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geosite", default="release/geosite.dat")
    args = parser.parse_args()

    groups = parse_geosite(args.geosite)
    direct = ["yandex", "ru-available-only-inside", "alibaba"]
    proxy = [
        "telegram",
        "meta",
        "youtube",
        "google",
        "reddit",
        "github",
        "ru-blocked",
        "antifilter-download-community",
        "openai",
        "anthropic",
        "tmdb",
        "kinopub",
        "tiktok",
        "roblox",
        "steam",
    ]
    block = ["category-ads", "win-spy"]

    membership = defaultdict(list)
    for code, entries in groups.items():
        for entry in entries:
            membership[key(entry)].append(code)
    duplicate_keys = {k: v for k, v in membership.items() if len(v) > 1}

    ru_blocked_entries = groups.get("ru-blocked", [])
    ru_blocked_ru = [key(e) for e in ru_blocked_entries if looks_ru_domain(e)]

    report = {
        "category_entry_counts": {k: len(groups.get(k, [])) for k in sorted(set(direct + proxy + block))},
        "duplicates_across_categories": {
            "count": len(duplicate_keys),
            "examples": [{"entry": k, "categories": v} for k, v in list(sorted(duplicate_keys.items()))[:30]],
        },
        "ru_blocked": {
            "total_entries": len(ru_blocked_entries),
            "entries_matching_direct_ru_su_rf_regex": len(ru_blocked_ru),
            "examples": ru_blocked_ru[:30],
        },
        "direct_proxy_exact_overlaps": [
            summarize_overlap(groups, d, p)
            for d in direct
            for p in proxy
            if summarize_overlap(groups, d, p)["count"] > 0
        ],
        "block_proxy_exact_overlaps": [
            summarize_overlap(groups, b, p)
            for b in block
            for p in proxy
            if summarize_overlap(groups, b, p)["count"] > 0
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
