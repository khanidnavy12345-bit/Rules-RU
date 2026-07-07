#!/usr/bin/env python3
import argparse
import sys


def read_varint(data, pos):
    value = 0
    shift = 0
    while shift < 64:
        if pos >= len(data):
            raise ValueError("unexpected EOF while reading varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise ValueError("varint too large")


def skip_field(data, pos, wire):
    if wire == 0:
        _, pos = read_varint(data, pos)
        return pos
    if wire == 1:
        end = pos + 8
    elif wire == 2:
        length, pos = read_varint(data, pos)
        end = pos + length
    elif wire == 5:
        end = pos + 4
    else:
        raise ValueError(f"unsupported wire type {wire}")
    if end > len(data):
        raise ValueError("field exceeds input")
    return end


def country_code(entry):
    pos = 0
    while pos < len(entry):
        key, pos = read_varint(entry, pos)
        field = key >> 3
        wire = key & 7
        if field == 1 and wire == 2:
            length, pos = read_varint(entry, pos)
            end = pos + length
            if end > len(entry):
                raise ValueError("country_code exceeds entry")
            return entry[pos:end].decode("utf-8").lower()
        pos = skip_field(entry, pos, wire)
    raise ValueError("country_code not found")


def repeated_field_count(entry, field_number):
    count = 0
    pos = 0
    while pos < len(entry):
        key, pos = read_varint(entry, pos)
        field = key >> 3
        wire = key & 7
        if field == field_number:
            count += 1
        pos = skip_field(entry, pos, wire)
    return count


def filter_dat(input_path, output_path, keep):
    keep = {item.strip().lower() for item in keep if item.strip()}
    data = open(input_path, "rb").read()
    output = bytearray()
    seen = []
    kept = []
    category_stats = {}
    pos = 0
    while pos < len(data):
        field_start = pos
        key, pos = read_varint(data, pos)
        field = key >> 3
        wire = key & 7
        if field != 1 or wire != 2:
            pos = skip_field(data, pos, wire)
            continue
        length, pos = read_varint(data, pos)
        entry_end = pos + length
        if entry_end > len(data):
            raise ValueError("top-level entry exceeds input")
        code = country_code(data[pos:entry_end])
        seen.append(code)
        if code in keep:
            output.extend(data[field_start:entry_end])
            kept.append(code)
            category_stats[code] = {
                "entries": repeated_field_count(data[pos:entry_end], 2),
                "bytes": entry_end - field_start,
            }
        pos = entry_end
    missing = sorted(keep - set(kept))
    if missing:
        raise SystemExit(f"missing categories: {', '.join(missing)}")
    if not output:
        raise SystemExit("no categories kept")
    with open(output_path, "wb") as f:
        f.write(output)
    return {"seen": len(seen), "kept": sorted(kept), "bytes": len(output), "categoryStats": category_stats}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", dest="output_path", required=True)
    parser.add_argument("--keep", required=True)
    args = parser.parse_args()
    result = filter_dat(args.input_path, args.output_path, args.keep.split(","))
    print(f"seen={result['seen']} kept={len(result['kept'])} bytes={result['bytes']}")
    print("kept_categories=" + ",".join(result["kept"]))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
