#!/usr/bin/env python3
"""
Build an interactive well <-> bioevent ("bug") network graph for Walker
Ridge (WR) paleo picks, as a single self-contained HTML file.

Pipeline (agreed interactively, see conversation record):
  1. Scope: paleo_headers.surface_area == 'WR'
  2. Exclude paleo_age values with no recognizable Latin binomial (lithology,
     structural/QC markers, "local marker" genus+code entries, bare
     chronostratigraphic-only picks, literal placeholders like
     "first sample examined" / "- - - - -").
  3. Split remaining paleo_age into (age, bug) on a leading
     (Lower|Middle|Upper)? Epoch (Substage)? prefix. 5 more distinct values
     failed this split (no age prefix present) and were excluded too.
  4. Mechanical bug normalization: collapse internal double-spaces; strip a
     trailing single capital letter when it directly follows a known event
     word (acme/extinction/increase/...). Plus two user-approved spelling
     fixes (druggii -> druggi, furcatolithioides -> furcatolithoides) that
     turned out to be renames, not merges (no pre-existing counterpart).

Output: wr_bioevent_network.html in this folder.
"""
import sqlite3
import re
import json
from collections import Counter, defaultdict
from pathlib import Path

FOLDER = Path(__file__).parent
DB = FOLDER / "paleo_boem.sqlite"
OUT_HTML = FOLDER / "wr_bioevent_network.html"

# --------------------------------------------------------------------------
# Step 1-3 pipeline (reproduced exactly as validated interactively)
# --------------------------------------------------------------------------
BINOMIAL_RE = re.compile(r'\b[A-Z][a-z]+\.?\s+[a-z][a-z\-]+\b')
EPOCH_LIST = ['Cretaceous', 'Paleocene', 'Eocene', 'Oligocene', 'Miocene',
              'Pliocene', 'Pleistocene', 'Holocene', 'Jurassic', 'Triassic']
EPOCHS_ALT = '(?:' + '|'.join(EPOCH_LIST) + ')'
AGE_RE = re.compile(rf'^((?:Lower|Middle|Upper)\s+)?({EPOCHS_ALT})(\s*\([A-Za-z]+\))?\s+')
NO_AGE_MATCH_EXCLUDE = {
    'Arenaceous faunal increase', 'Bathysiphon fauna',
    'Cyclammina cancellata (local marker)', 'Cyclammina sp. (local marker)',
    'Turtle Grass fauna = Amphistegina Mound',
}
EVENT_WORDS = (r'(?:acme|extinction|increase|decrease|FAD|LAD|top|base|flood|'
               r'spike|appearance|disappearance|influx|abundant|common|rare|'
               r'coil-change\s+(?:right-to-left|left-to-right))')
TRAILING_LETTER_RE = re.compile(rf'^(.*\b{EVENT_WORDS})\s+([A-Z])$', re.I)
EXTRA_SPELLING_FIX = {
    'Discoaster druggii': 'Discoaster druggi',
    'Sphenolithus furcatolithioides': 'Sphenolithus furcatolithoides',
}

EPOCH_ORDER = ['Cretaceous', 'Paleocene', 'Eocene', 'Oligocene', 'Miocene',
               'Pliocene', 'Pleistocene', 'Holocene']
EPOCH_COLOR = {
    'Cretaceous': '#313695',
    'Paleocene': '#4575b4',
    'Eocene': '#74add1',
    'Oligocene': '#abd9e9',
    'Miocene': '#fee090',
    'Pliocene': '#fdae61',
    'Pleistocene': '#f46d43',
    'Holocene': '#a50026',
    'Unknown': '#999999',
}


def fix_spelling(canon):
    for wrong, right in EXTRA_SPELLING_FIX.items():
        if canon == wrong:
            return right
        if canon.startswith(wrong + ' '):
            return right + canon[len(wrong):]
    return canon


def extract_epoch(age):
    m = re.search(EPOCHS_ALT, age)
    return m.group(0) if m else 'Unknown'


def load_kept_picks():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT h.api_well_number, h.well_name, h.surface_block, h.lease_number,
               p.paleo_report_id, p.paleo_age, p.paleo_sample_md,
               p.age_definite_possible, p.age_at_in
        FROM paleo_picks p
        JOIN paleo_headers h
          ON p.api_well_number = h.api_well_number
         AND p.paleo_report_id = h.paleo_report_id
        WHERE h.surface_area = 'WR'
    """)
    rows = cur.fetchall()
    conn.close()

    kept = []
    for r in rows:
        v = r["paleo_age"]
        if not BINOMIAL_RE.search(v) or v in NO_AGE_MATCH_EXCLUDE:
            continue
        m = AGE_RE.match(v)
        if not m:
            continue
        age = m.group(0).strip()
        bug_raw = re.sub(r'\s{2,}', ' ', v[m.end():].strip())
        m2 = TRAILING_LETTER_RE.match(bug_raw)
        canon = m2.group(1).strip() if m2 else bug_raw
        canon = fix_spelling(canon)
        kept.append({
            "api": r["api_well_number"],
            "well_name": (r["well_name"] or "").strip(),
            "block": (r["surface_block"] or "").strip(),
            "lease": (r["lease_number"] or "").strip(),
            "report_id": r["paleo_report_id"],
            "age": age,
            "epoch": extract_epoch(age),
            "bug": canon,
            "md": r["paleo_sample_md"],
            "conf": r["age_definite_possible"],
            "atin": r["age_at_in"],
        })
    return kept


def build_graph(kept):
    well_meta = {}
    for k in kept:
        well_meta.setdefault(k["api"], {
            "well_name": k["well_name"], "block": k["block"], "lease": k["lease"],
            "report_ids": set(),
        })
        well_meta[k["api"]]["report_ids"].add(k["report_id"])

    bug_epoch_counts = defaultdict(Counter)
    bug_ages = defaultdict(set)
    bug_wells = defaultdict(set)
    edge_agg = defaultdict(lambda: {"count": 0, "def_count": 0, "pos_count": 0,
                                     "ages": set(), "mds": []})

    for k in kept:
        bug_epoch_counts[k["bug"]][k["epoch"]] += 1
        bug_ages[k["bug"]].add(k["age"])
        bug_wells[k["bug"]].add(k["api"])
        e = edge_agg[(k["api"], k["bug"])]
        e["count"] += 1
        if k["conf"] == "DEF":
            e["def_count"] += 1
        elif k["conf"] == "POS":
            e["pos_count"] += 1
        e["ages"].add(k["age"])
        if k["md"] is not None:
            e["mds"].append(k["md"])

    nodes = []
    for api, meta in sorted(well_meta.items()):
        label = f"WR {meta['block']} #{meta['well_name']}" if meta['block'] else meta['well_name'] or api
        nodes.append({
            "id": f"well::{api}",
            "label": label,
            "group": "well",
            "shape": "square",
            "color": "#4c566a",
            "api": api,
            "lease": meta["lease"],
            "block": meta["block"],
            "reportCount": len(meta["report_ids"]),
        })

    max_degree = max((len(w) for w in bug_wells.values()), default=1)
    for bug, epoch_counter in sorted(bug_epoch_counts.items()):
        top_epoch = epoch_counter.most_common(1)[0][0]
        degree = len(bug_wells[bug])
        size = 8 + 22 * (degree / max_degree) ** 0.5
        nodes.append({
            "id": f"bug::{bug}",
            "label": bug,
            "group": "bug",
            "shape": "dot",
            "color": EPOCH_COLOR.get(top_epoch, EPOCH_COLOR["Unknown"]),
            "epoch": top_epoch,
            "ages": sorted(bug_ages[bug]),
            "wellDegree": degree,
            "totalPicks": sum(epoch_counter.values()),
            "size": round(size, 1),
        })

    edges = []
    for (api, bug), agg in edge_agg.items():
        width = 1 + 1.2 * (agg["count"] - 1)
        def_width = 1 + 1.2 * (agg["def_count"] - 1) if agg["def_count"] > 0 else 0
        edges.append({
            "from": f"well::{api}",
            "to": f"bug::{bug}",
            "width": round(width, 1),
            "defWidth": round(def_width, 1),
            "count": agg["count"],
            "defCount": agg["def_count"],
            "posCount": agg["pos_count"],
            "ages": sorted(agg["ages"]),
            "mds": sorted(x for x in agg["mds"] if x is not None),
        })

    return nodes, edges


def main():
    kept = load_kept_picks()
    nodes, edges = build_graph(kept)

    well_nodes = [n for n in nodes if n["group"] == "well"]
    bug_nodes = [n for n in nodes if n["group"] == "bug"]
    print(f"kept picks: {len(kept)}")
    print(f"well nodes: {len(well_nodes)}  bug nodes: {len(bug_nodes)}  edges: {len(edges)}")

    graph_data = {
        "nodes": nodes,
        "edges": edges,
        "epochOrder": EPOCH_ORDER,
        "epochColor": EPOCH_COLOR,
        "stats": {
            "wells": len(well_nodes),
            "bugs": len(bug_nodes),
            "edges": len(edges),
            "picks": len(kept),
        },
    }

    template = (FOLDER / "wr_bioevent_network.template.html").read_text(encoding="utf-8")
    html = template.replace("__GRAPH_DATA_JSON__", json.dumps(graph_data))
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
