# BOEM Paleontologic (Biostratigraphy) Data — 2026-09-05

## Source

- Download: https://www.data.boem.gov/Paleo/Files/gepaldmp_all.zip
  ("Releasable Paleo Reports" download, linked from
  https://www.data.boem.gov/Main/Paleo.aspx)
- Field layout: https://www.data.boem.gov/Main/HtmlPage.aspx?page=newPaleo,
  reproduced in `gepaldmp.pdf` (shipped alongside the data). All byte offsets
  used by the parser were verified against this document before writing any
  parsing code.
- Report window: 1947-01-01 through 2026-08-31. The layout doc's own header
  states **211,791 records were dumped**, which matches the total record
  count parsed here exactly (14,732 header + 197,059 pick records).
- Download date: 2026-09-05.

Note: this session's network egress is blocked to `www.data.boem.gov`, so the
raw dump could not be fetched directly here. The user downloaded it manually
and uploaded it into this folder, split into pieces to work around upload
size limits:
- `gepaldmp_Header.txt` — all header ('H') records, one file
- `gepaldmp_Paleo_part1.txt` / `gepaldmp_Paleo_part2.txt` — pick ('P')
  records, split across two files (simple sequential split, not a semantic
  partition — the parser just concatenates them in order)

## File format as actually found

Contrary to the possibility flagged going in, **this download is not a
single mixed-record-type file** — BOEM shipped Header and Paleo/pick records
as cleanly separate files, each containing exactly one Record Type:

| File | Record Type | Records | Line ending | Line length |
|---|---|---|---|---|
| `gepaldmp_Header.txt` | `H` | 14,732 | CRLF | variable, 269–2,254 chars (fixed portion is 269 chars; Remarks is appended, variable-length, no padding) |
| `gepaldmp_Paleo_part1.txt` | `P` | 98,529 | LF | fixed, 138 chars |
| `gepaldmp_Paleo_part2.txt` | `P` | 98,530 | LF | fixed, 138 chars |

Encoding: **Windows-1252 (cp1252)**, not plain ASCII or UTF-8. Two header
records contain smart-quote characters (0x92 right single quote used as a
foot mark, e.g. `24800’-24830’`; 0x93 left double quote) that only decode
correctly as cp1252. All files are read with `encoding="cp1252"`.

## Field layout used

Byte offsets exactly as specified in `gepaldmp.pdf` / BOEM's official layout
page, and confirmed field-by-field against sample records before trusting
them at scale (see commit history / development notes for the verification
commands). No discrepancies were found between the documented layout and the
actual file structure, so no offsets were reinterpreted.

**Header record (`H`)** — fixed portion is 269 characters (byte positions
1–269 in the 1-indexed spec), then `Remarks` runs from position 270 to end
of line, variable length:

```
001-001  Record Type              033-037  Well Name
002-013  API Well Number          038-038  Public Information Code
014-015  Paleo Report ID Number   039-046  Public Release Date (MMDDYYYY)
016-017  Total Reports for API    047-054  Paleo Effective Date (MMDDYYYY)
018-019  Surface Area             055-089  Paleo Report Source
020-025  Surface Block            090-126  Paleo Reporter
026-032  Lease Number             127-176  Drilling Operator
177-181  High Sample              203-207  RKB Elevation
182-186  Low Sample               208-212  Water Depth
187-187  Ecozone EQ MMS           213-228  Surface X Coordinate
188-192  First Sample Examined    229-244  Surface Y Coordinate
193-197  Borehole Measured Depth  245-256  Surface Latitude
198-202  True Vertical Depth      257-269  Surface Longitude
270-end  Remarks (variable length)
```

**Paleo / pick record (`P`)** — fixed 138 characters:

```
001-001  Record Type              031-032  At/In (age)
002-013  API Well Number          033-132  Paleo Age (free text)
014-015  Paleo Report ID Number   133-135  Definite/Possible (ecozone)
016-017  Total Reports for API    136-137  At/In (ecozone)
018-022  Paleo Sample (MD)        138-138  Ecozone
023-027  Paleo Sample (TVD)
028-030  Definite/Possible (age)
```

## Parsing decisions

- **Text fields**: trimmed with `.strip()` (both sides). Verified before
  trimming that leading whitespace in fixed CHAR fields (`well_name`,
  `lease_number`, `surface_block`, `paleo_age`) is right-justification
  padding, not meaningful content — e.g. `well_name` is a short numeric-style
  code like `"  001"`, and `Paleo Age` placeholder rows are stored as
  `" - - - - -"` with padding on both sides. `Remarks` has no leading or
  trailing padding in any record (each line simply ends where the text
  ends), so `.strip()` there only removes the trailing CR left over from
  splitting on `\r\n`.
- **Numeric fields**: blank (all-space) → `NULL`, not zero. Fields that only
  ever contained whole numbers in this dump (sample depths, elevations,
  water depth) are still parsed as floats defensively, since the BOEM spec
  marks them all as generic `NUM` and some sibling fields in the same layout
  (coordinates, lat/lon) do carry decimals.
- **Dates**: `MMDDYYYY` → ISO `YYYY-MM-DD`. Zero blank or malformed dates
  were found in this dump (see Data Quality Summary), but the parser still
  nulls out and flags anything that doesn't parse as a valid calendar date,
  rather than assuming the format always holds.
- **Join key**: `(api_well_number, paleo_report_id)`, matching BOEM's own
  documented relationship between the two record types.
- **Record Type**: kept as a raw passthrough column in the header CSV (and
  paleo CSV) for traceability, but dropped from the SQLite tables since it's
  constant within each table (`H` / `P`) and redundant there.
- **Provenance**: every parsed row carries `source_file` and `source_line`
  so any row can be traced back to its exact byte-for-byte input line.

## Outputs

- `paleo_headers.csv` / `paleo_picks.csv` — full parsed tables, plain CSV,
  UTF-8, for spot-checking without a database client.
- `paleo_boem.sqlite` — same data loaded into two tables:
  - `paleo_headers` — primary key `(api_well_number, paleo_report_id)`
  - `paleo_picks` — foreign key `(api_well_number, paleo_report_id)` →
    `paleo_headers`, indexed for the join
- `data_quality_summary.md` — row counts, Record Type distribution, join
  integrity check results, and the Surface Area (protraction area) code
  distribution. Regenerate any of the above by re-running
  `parse_boem_paleo.py`.

## Data quality findings

This dump parsed essentially perfectly — see `data_quality_summary.md` for
the full numbers, but in short:

- All 211,791 records parsed with zero fixed-width structural failures
  (no short header lines, no mis-sized pick lines).
- Zero numeric/date field parse errors.
- Zero duplicate header keys, zero orphaned picks, zero headers without at
  least one matching pick — the join on `(api_well_number,
  paleo_report_id)` is fully clean.
- 48 distinct Surface Area (protraction area) codes are present, topped by
  `HI`, `WC`, `EI`, `GC`, and `MC`.

No manual reinterpretation of the BOEM spec was needed — the actual file
structure matched the documented layout exactly.

## Walker Ridge (WR) well &harr; bioevent network

`build_wr_network.py` derives a second, downstream artifact scoped to
`surface_area = 'WR'`: `wr_bioevent_network.html`, an interactive graph
linking wells to the biostratigraphic events ("bugs") picked in them.
`wr_bioevent_network.template.html` is the HTML/JS shell it fills in
(vis-network, loaded from cdnjs — no other external dependency).

**Pipeline** (agreed interactively): exclude `paleo_age` picks with no
recognizable Latin binomial (lithology, structural/QC markers, informal
"local marker" genus+code entries, bare chronostratigraphic-only picks,
literal placeholders like "first/last sample examined" and "- - - - -");
split the remainder into `age` + `bug` on a leading epoch/substage prefix;
mechanically normalize `bug` (collapse double-spaces, strip a trailing
single-letter tag after a known event word, plus two confirmed spelling
fixes — `druggii`→`druggi`, `furcatolithioides`→`furcatolithoides` — that
turned out to be renames, not merges, since neither had a pre-existing
correctly-spelled counterpart in WR). Result: 4,771 of 7,629 WR picks kept,
106 wells, 422 canonical bugs, 4,360 well&ndash;bug edges.

**Age color scale:** bug nodes are colored on a continuous viridis scale
across a 31-position canonical chronostratigraphic ordering (oldest =
Lower Cretaceous (Hauterivian), youngest = Holocene; see `AGE_ORDER` in
`build_wr_network.py`). Node size encodes degree centrality (# distinct
WR wells that bug was picked in; range 1&ndash;55, median 5). Only 3 of
422 bugs are picked at more than one age — their color is the average of
those ages' positions ("blended"), not a pick of one side.

**Flagged anomaly — not silently corrected:** 3 picks (bug
`Inaperturopollenites hiatus increase`, wells 608124014200 and
608124015001) are labeled `Upper Paleocene (Danian)` in the source data.
Danian is conventionally the *oldest* Paleocene stage, not the youngest,
so this is a chronostratigraphic inconsistency — confirmed present in the
raw text, not a parsing artifact. It's also **not a single-source typo**:
the label comes from two independent reporters (BUGWARE Inc. and
PetroStrat Ltd) on recent (2023&ndash;2024) reports for Shell Offshore
wells. Per direction, this is kept as its own distinct age label (colored
at the Danian position, since that's where the named stage belongs) and
flagged in the graph (dashed orange node border, tooltip note, sidebar
legend note) rather than corrected or merged away — treated as an open
question about the source data worth another look, not a bug to patch
around.

`Middle Pleistocene (Ionian)` is kept as its own label too, per direction
— "Ionian" is an older informal name for what's now formally called
Chibanian, not an error.

**Implementation note:** vis-network's automatic per-group color palette
silently overrides an explicit per-node `color` once two distinct `group`
values are each present in quantity (verified via isolated repro — one
group is fine, two is not). The shipped page works around this by not
passing a `group` key into vis's node objects at all; well vs. bug is
distinguished by shape plus the app's own filtering logic instead.
