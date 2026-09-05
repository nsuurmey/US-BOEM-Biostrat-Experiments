#!/usr/bin/env python3
"""
Parse BOEM's fixed-width GEPALDMP paleo dump (Header + Paleo/picks records)
into clean, joinable tables and load them into SQLite.

Source: https://www.data.boem.gov/Paleo/Files/gepaldmp_all.zip
Layout: https://www.data.boem.gov/Main/HtmlPage.aspx?page=newPaleo
        (confirmed against the layout doc shipped alongside the data,
        gepaldmp.pdf, in this same folder)

Inputs (this folder):
  gepaldmp_Header.txt        - all 'H' header records, CRLF line endings
  gepaldmp_Paleo_part1.txt   - 'P' pick records, part 1 of 2 (LF line endings)
  gepaldmp_Paleo_part2.txt   - 'P' pick records, part 2 of 2 (LF line endings)

Outputs (this folder):
  paleo_headers.csv
  paleo_picks.csv
  paleo_boem.sqlite
  data_quality_summary.md
"""
import csv
import re
import sqlite3
from collections import Counter
from pathlib import Path

FOLDER = Path(__file__).parent
ENCODING = "cp1252"  # confirmed via byte inspection: 0x92/0x93 = CP1252 smart quotes in Remarks

HEADER_FILE = FOLDER / "gepaldmp_Header.txt"
PALEO_FILES = [FOLDER / "gepaldmp_Paleo_part1.txt", FOLDER / "gepaldmp_Paleo_part2.txt"]

# --------------------------------------------------------------------------
# Field layouts (1-indexed start/len from BOEM's spec, converted to 0-indexed
# Python slices [start, end) here). Verified against real file offsets before
# writing this script.
# --------------------------------------------------------------------------

HEADER_FIELDS = [
    # (name, start0, end0, kind)   kind in {"str","int","float","date"}
    ("record_type", 0, 1, "str"),
    ("api_well_number", 1, 13, "str"),
    ("paleo_report_id", 13, 15, "int"),
    ("total_reports_for_api", 15, 17, "int"),
    ("surface_area", 17, 19, "str"),
    ("surface_block", 19, 25, "str"),
    ("lease_number", 25, 32, "str"),
    ("well_name", 32, 37, "str"),
    ("public_information_code", 37, 38, "str"),
    ("public_release_date", 38, 46, "date"),
    ("paleo_effective_date", 46, 54, "date"),
    ("paleo_report_source", 54, 89, "str"),
    ("paleo_reporter", 89, 126, "str"),
    ("drilling_operator", 126, 176, "str"),
    ("high_sample", 176, 181, "float"),
    ("low_sample", 181, 186, "float"),
    ("ecozone_eq_mms", 186, 187, "str"),
    ("first_sample_examined", 187, 192, "float"),
    ("borehole_measured_depth", 192, 197, "float"),
    ("true_vertical_depth", 197, 202, "float"),
    ("rkb_elevation", 202, 207, "float"),
    ("water_depth", 207, 212, "float"),
    ("surface_x_coordinate", 212, 228, "float"),
    ("surface_y_coordinate", 228, 244, "float"),
    ("surface_latitude", 244, 256, "float"),
    ("surface_longitude", 256, 269, "float"),
    # remarks: variable length, goes to end of line -- handled separately
]
HEADER_FIXED_LEN = 269  # everything before Remarks

PALEO_FIELDS = [
    ("record_type", 0, 1, "str"),
    ("api_well_number", 1, 13, "str"),
    ("paleo_report_id", 13, 15, "int"),
    ("total_reports_for_api", 15, 17, "int"),
    ("paleo_sample_md", 17, 22, "float"),
    ("paleo_sample_tvd", 22, 27, "float"),
    ("age_definite_possible", 27, 30, "str"),
    ("age_at_in", 30, 32, "str"),
    ("paleo_age", 32, 132, "str"),
    ("ecozone_definite_possible", 132, 135, "str"),
    ("ecozone_at_in", 135, 137, "str"),
    ("ecozone", 137, 138, "str"),
]
PALEO_LEN = 138


def parse_num(raw, kind):
    v = raw.strip()
    if v == "":
        return None
    try:
        return int(v) if kind == "int" else float(v)
    except ValueError:
        return ("__PARSE_ERROR__", raw)


def parse_date(raw):
    v = raw.strip()
    if v == "":
        return None
    m = re.match(r"^(\d{2})(\d{2})(\d{4})$", v)
    if not m:
        return ("__PARSE_ERROR__", raw)
    mm, dd, yyyy = m.groups()
    try:
        from datetime import date
        d = date(int(yyyy), int(mm), int(dd))
        return d.isoformat()
    except ValueError:
        return ("__PARSE_ERROR__", raw)


def extract(raw_line, fields):
    row = {}
    errors = []
    for name, s, e, kind in fields:
        chunk = raw_line[s:e]
        if kind == "str":
            row[name] = chunk.strip()
        elif kind == "date":
            result = parse_date(chunk)
            if isinstance(result, tuple):
                errors.append((name, result[1]))
                row[name] = None
            else:
                row[name] = result
        else:
            result = parse_num(chunk, kind)
            if isinstance(result, tuple):
                errors.append((name, result[1]))
                row[name] = None
            else:
                row[name] = result
    return row, errors


def parse_headers():
    raw_bytes = HEADER_FILE.read_bytes()
    lines = raw_bytes.split(b"\r\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]

    rows = []
    parse_error_count = 0
    short_line_count = 0
    for lineno, raw in enumerate(lines, start=1):
        text = raw.decode(ENCODING)
        if len(text) < HEADER_FIXED_LEN:
            short_line_count += 1
            continue
        row, errors = extract(text, HEADER_FIELDS)
        row["remarks"] = text[HEADER_FIXED_LEN:].strip()
        row["source_file"] = HEADER_FILE.name
        row["source_line"] = lineno
        if errors:
            parse_error_count += 1
        rows.append(row)
    return rows, parse_error_count, short_line_count


def parse_paleo():
    rows = []
    parse_error_count = 0
    bad_len_count = 0
    for path in PALEO_FILES:
        raw_bytes = path.read_bytes()
        lines = raw_bytes.split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]
        for lineno, raw in enumerate(lines, start=1):
            text = raw.decode(ENCODING)
            if len(text) != PALEO_LEN:
                bad_len_count += 1
                continue
            row, errors = extract(text, PALEO_FIELDS)
            row["source_file"] = path.name
            row["source_line"] = lineno
            if errors:
                parse_error_count += 1
            rows.append(row)
    return rows, parse_error_count, bad_len_count


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def load_sqlite(db_path, headers, picks):
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE paleo_headers (
            api_well_number TEXT NOT NULL,
            paleo_report_id INTEGER NOT NULL,
            total_reports_for_api INTEGER,
            surface_area TEXT,
            surface_block TEXT,
            lease_number TEXT,
            well_name TEXT,
            public_information_code TEXT,
            public_release_date TEXT,
            paleo_effective_date TEXT,
            paleo_report_source TEXT,
            paleo_reporter TEXT,
            drilling_operator TEXT,
            high_sample REAL,
            low_sample REAL,
            ecozone_eq_mms TEXT,
            first_sample_examined REAL,
            borehole_measured_depth REAL,
            true_vertical_depth REAL,
            rkb_elevation REAL,
            water_depth REAL,
            surface_x_coordinate REAL,
            surface_y_coordinate REAL,
            surface_latitude REAL,
            surface_longitude REAL,
            remarks TEXT,
            source_file TEXT,
            source_line INTEGER,
            PRIMARY KEY (api_well_number, paleo_report_id)
        )
    """)
    cur.execute("""
        CREATE TABLE paleo_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_well_number TEXT NOT NULL,
            paleo_report_id INTEGER NOT NULL,
            total_reports_for_api INTEGER,
            paleo_sample_md REAL,
            paleo_sample_tvd REAL,
            age_definite_possible TEXT,
            age_at_in TEXT,
            paleo_age TEXT,
            ecozone_definite_possible TEXT,
            ecozone_at_in TEXT,
            ecozone TEXT,
            source_file TEXT,
            source_line INTEGER,
            FOREIGN KEY (api_well_number, paleo_report_id)
                REFERENCES paleo_headers (api_well_number, paleo_report_id)
        )
    """)

    header_cols = [f[0] for f in HEADER_FIELDS if f[0] != "record_type"] + ["remarks", "source_file", "source_line"]
    cur.executemany(
        f"INSERT OR IGNORE INTO paleo_headers ({','.join(header_cols)}) VALUES ({','.join('?' * len(header_cols))})",
        [tuple(h[c] for c in header_cols) for h in headers],
    )

    pick_cols = [f[0] for f in PALEO_FIELDS] + ["source_file", "source_line"]
    pick_cols_no_rectype = [c for c in pick_cols if c != "record_type"]
    cur.executemany(
        f"INSERT INTO paleo_picks ({','.join(pick_cols_no_rectype)}) VALUES ({','.join('?' * len(pick_cols_no_rectype))})",
        [tuple(p[c] for c in pick_cols_no_rectype) for p in picks],
    )

    cur.execute("CREATE INDEX idx_picks_join ON paleo_picks(api_well_number, paleo_report_id)")
    conn.commit()
    return conn


def main():
    headers, header_parse_errors, header_short_lines = parse_headers()
    picks, pick_parse_errors, pick_bad_len = parse_paleo()

    header_keys = Counter((h["api_well_number"], h["paleo_report_id"]) for h in headers)
    dup_header_keys = {k: v for k, v in header_keys.items() if v > 1}

    pick_keys = set((p["api_well_number"], p["paleo_report_id"]) for p in picks)
    header_key_set = set(header_keys)

    orphan_picks = [p for p in picks if (p["api_well_number"], p["paleo_report_id"]) not in header_key_set]
    headers_without_picks = [k for k in header_key_set if k not in pick_keys]

    rec_type_counts_header = Counter(h["record_type"] for h in headers)
    rec_type_counts_pick = Counter(p["record_type"] for p in picks)
    surface_area_counts = Counter(h["surface_area"] for h in headers)

    header_fieldnames = [f[0] for f in HEADER_FIELDS] + ["remarks", "source_file", "source_line"]
    pick_fieldnames = [f[0] for f in PALEO_FIELDS] + ["source_file", "source_line"]

    write_csv(FOLDER / "paleo_headers.csv", headers, header_fieldnames)
    write_csv(FOLDER / "paleo_picks.csv", picks, pick_fieldnames)

    conn = load_sqlite(FOLDER / "paleo_boem.sqlite", headers, picks)
    conn.close()

    lines = []
    lines.append("# BOEM Paleo Data Quality Summary\n")
    lines.append(f"Generated by `parse_boem_paleo.py`.\n")
    lines.append("## Row counts\n")
    lines.append(f"- Header file records parsed: {len(headers)}")
    lines.append(f"- Header records skipped (shorter than {HEADER_FIXED_LEN} chars): {header_short_lines}")
    lines.append(f"- Paleo/pick records parsed: {len(picks)}")
    lines.append(f"- Paleo/pick records skipped (length != {PALEO_LEN} chars): {pick_bad_len}")
    lines.append(f"- Total parsed records: {len(headers) + len(picks)} (BOEM dump header states 211,791 total)\n")

    lines.append("## Record Type distribution\n")
    lines.append(f"- Header file Record Types: {dict(rec_type_counts_header)}")
    lines.append(f"- Paleo file Record Types: {dict(rec_type_counts_pick)}\n")

    lines.append("## Field-level parse errors (numeric/date fields that didn't match expected format)\n")
    lines.append(f"- Header records with >=1 field parse error: {header_parse_errors}")
    lines.append(f"- Pick records with >=1 field parse error: {pick_parse_errors}\n")

    lines.append("## Join integrity: paleo_headers <-> paleo_picks on (api_well_number, paleo_report_id)\n")
    lines.append(f"- Distinct header keys: {len(header_key_set)}")
    lines.append(f"- Duplicate header keys (same API + report ID appearing >1x in header file): {len(dup_header_keys)}")
    if dup_header_keys:
        sample = list(dup_header_keys.items())[:10]
        lines.append(f"  - Sample duplicates: {sample}")
    lines.append(f"- Orphaned picks (no matching header row): {len(orphan_picks)}")
    if orphan_picks:
        sample_keys = list({(p['api_well_number'], p['paleo_report_id']) for p in orphan_picks})[:10]
        lines.append(f"  - Sample orphan keys: {sample_keys}")
    lines.append(f"- Header rows with no matching picks: {len(headers_without_picks)}")
    if headers_without_picks:
        lines.append(f"  - Sample: {headers_without_picks[:10]}")
    lines.append("")

    lines.append("## Surface Area (protraction area) distribution in paleo_headers\n")
    lines.append("| Surface Area | Header rows |")
    lines.append("|---|---|")
    for area, count in surface_area_counts.most_common():
        lines.append(f"| {area or '(blank)'} | {count} |")
    lines.append("")

    (FOLDER / "data_quality_summary.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
