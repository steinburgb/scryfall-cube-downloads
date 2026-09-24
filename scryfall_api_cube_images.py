"""
Download Magic: The Gathering card images from Scryfall for a cube list.

Reads a CSV (e.g. a CubeCobra export) with at least the columns
"Set" and "Collector Number", looks each card up on Scryfall, and saves
PNG images into one of three layouts:

  colour   <out>/<Color>/<set>_<number>.png                     (default)
  rarity   <out>/<Rarity>/<Color>/<set>_<number>.png
  sheets   <out>/Sheet_fronts_N/<slot> <set>_<number>.png        (69 per folder)
           <out>/Sheet_backs_N/<slot> <set>_<number>_back.png

In sheets mode, --assemble composes each folder into one grid image for
Tabletop Simulator (<out>/cardsheets/<folder>.png) with the card back in the
last slot, plus a JSON file recording the grid size and which card is in
which slot. --assemble-only rebuilds the sheets without downloading anything.

Double-faced cards are detected automatically from Scryfall's data, so the
manual "double-sided" tag is no longer required (it is still honoured as a
sanity check: a tagged card that Scryfall says is single-faced prints a warning).

Usage:
  python scryfall_api_cube_images.py cube.csv
  python scryfall_api_cube_images.py cube.csv --out ./images --mode sheets --assemble
  python scryfall_api_cube_images.py cube.csv --out ./images --mode sheets --assemble-only
  python scryfall_api_cube_images.py --help
"""

import argparse
import csv
import hashlib
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# --- Constants ---------------------------------------------------------------

SCRYFALL_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
COLLECTION_BATCH_SIZE = 75          # Scryfall's maximum identifiers per request
DEFAULT_DELAY = 0.1                 # Seconds between requests (Scryfall asks for 50-100 ms)
REQUEST_TIMEOUT = 30                # Seconds before a stalled request is abandoned
MAX_RETRIES = 4                     # Attempts per request on 429 / 5xx / network errors
CARD_BACK_NAME = "00 Back.png"      # Shipped with the repo; "00" sorts it first in a folder
CARD_BACK_FALLBACK_URL = (
    "https://raw.githubusercontent.com/steinburgb/scryfall-cube-downloads/"
    "refs/heads/main/00%20Back.png"
)

HEADERS = {
    # Scryfall requires a descriptive User-Agent and an Accept header.
    "User-Agent": "MTGCubeImageDownloader/2.0 (https://github.com/steinburgb/scryfall-cube-downloads)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}

CARD_W, CARD_H = 745, 1040          # Scryfall "png" image size; sheet tiles use this ratio
DEFAULT_TILE_WIDTH = 488            # Sheet tile width: a full 10x7 sheet comes out 4880 px wide
MAX_COLS, MAX_ROWS = 10, 7          # Tabletop Simulator's largest card sheet (70 slots)
SLOT_FILE_RE = re.compile(r"^(\d+) (.+)\.png$")   # "<slot> <set>_<number>[_back].png"

MANIFEST_FIELDS = [
    "file", "name", "set", "collector_number", "scryfall_id", "face",
    "source_url", "downloaded_at", "sha256",
]

# One row per slot in <sheet>.csv; the sheet-level columns repeat on every row
# so the file stands on its own in a spreadsheet.
SHEET_FIELDS = [
    "sheet", "columns", "rows", "tile_width", "tile_height", "sheet_width", "sheet_height",
    "slot", "column", "row", "file", "name", "set", "collector_number", "scryfall_id", "face",
]
# One row per sheet in cardsheets/sheets.csv.
SHEET_INDEX_FIELDS = [
    "sheet", "source_folder", "columns", "rows", "cards", "back_slot",
    "tile_width", "tile_height", "sheet_width", "sheet_height", "manifest",
]


# --- Command line ------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Download card images from Scryfall for a cube CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("csv", type=Path, help="Input CSV (e.g. a CubeCobra export)")
    p.add_argument("--out", type=Path,
                   default=Path.home() / "Downloads" / "Scryfall_Images",
                   help="Folder to save images into")
    p.add_argument("--mode", choices=["colour", "rarity", "sheets"], default="colour",
                   help="colour: folders by colour; rarity: rarity/colour; "
                        "sheets: sequential batches for card-sheet creation")
    p.add_argument("--sheet-size", type=int, default=69,
                   help="Cards per folder in sheets mode (69 + card back = 70 slots)")
    p.add_argument("--color-column", default="Color",
                   help="CSV column used for colour folder names (e.g. \"Color Category\")")
    p.add_argument("--rarity-column", default="Rarity",
                   help="CSV column used for rarity folder names")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help="Seconds to wait between requests")
    p.add_argument("--skip-maybeboard", action="store_true",
                   help="Skip rows whose \"maybeboard\" column is true (CubeCobra exports include them)")
    p.add_argument("--force", action="store_true",
                   help="Re-download files that already exist")
    p.add_argument("--no-card-back", action="store_true",
                   help="In sheets mode, do not copy the card back into each folder")
    p.add_argument("--assemble", action="store_true",
                   help="After downloading in sheets mode, compose each folder into a grid image")
    p.add_argument("--assemble-only", action="store_true",
                   help="Only compose sheets from folders already in --out; download nothing")
    p.add_argument("--grid", default="auto",
                   help="Sheet grid: 'auto' (near-square, max 10x7) or fixed COLSxROWS, e.g. 10x7")
    p.add_argument("--tile-width", type=int, default=DEFAULT_TILE_WIDTH,
                   help="Width in pixels of each card on the sheet (height follows the card ratio; "
                        f"{CARD_W} is Scryfall's native size)")
    args = p.parse_args(argv)

    if (args.assemble or args.assemble_only) and args.mode != "sheets":
        p.error("--assemble / --assemble-only require --mode sheets")
    if args.grid != "auto":
        m = re.fullmatch(r"(\d+)x(\d+)", args.grid)
        if not m or not (1 <= int(m.group(1)) <= MAX_COLS and 1 <= int(m.group(2)) <= MAX_ROWS):
            p.error(f"--grid must be 'auto' or COLSxROWS with at most {MAX_COLS}x{MAX_ROWS}")
        args.grid = (int(m.group(1)), int(m.group(2)))
    if args.mode == "sheets" and args.sheet_size > MAX_COLS * MAX_ROWS - 1:
        p.error(f"--sheet-size cannot exceed {MAX_COLS * MAX_ROWS - 1} (one slot is kept for the card back)")
    return args


# --- HTTP helpers ------------------------------------------------------------

def request_with_retry(session, method, url, delay, **kwargs):
    """
    Make one HTTP request, retrying on rate limits (429), server errors (5xx)
    and network failures. Always waits `delay` seconds afterwards so the
    pause happens even when the request failed.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = session.request(method, url, **kwargs)
            if res.status_code == 429 or res.status_code >= 500:
                # Honour Retry-After if Scryfall sends one; otherwise back off exponentially.
                wait = float(res.headers.get("Retry-After", 2 ** attempt))
                print(f"\nHTTP {res.status_code} from {url} - waiting {wait:.0f}s (attempt {attempt}/{MAX_RETRIES})")
                res.close()
                time.sleep(wait)
                continue
            res.raise_for_status()
            return res
        except requests.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
        finally:
            time.sleep(delay)
    raise RuntimeError(f"Giving up on {url}: {last_error}")


def download_file(session, url, dest, delay):
    """
    Download `url` to `dest`. Writes to a temporary ".part" file first and
    renames it on success, so an interrupted download never leaves a
    half-written image behind. Returns the SHA-256 checksum of the file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    sha = hashlib.sha256()
    try:
        res = request_with_retry(session, "GET", url, delay, stream=True)
        with open(tmp, "wb") as f:
            for chunk in res.iter_content(64 * 1024):
                f.write(chunk)
                sha.update(chunk)
        os.replace(tmp, dest)        # atomic on the same filesystem
    finally:
        if tmp.exists():
            tmp.unlink()
    return sha.hexdigest()


# --- CSV input ---------------------------------------------------------------

def read_cube_csv(path, color_column="Color", rarity_column="Rarity", skip_maybeboard=False):
    """
    Read the cube CSV and return a list of dicts with normalised keys.
    Uses utf-8-sig so a leading byte-order mark (BOM) does not get glued
    onto the first column name. Header lookup is case-insensitive.
    With skip_maybeboard=True, rows whose "maybeboard" cell is true are left out.
    """
    rows = []
    skipped_maybe = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            sys.exit(f"Error: {path} has no header row.")
        # Map lowercase header -> original header so "Tags" and "tags" both work.
        headers = {h.strip().lower(): h for h in reader.fieldnames}

        def get(row, name):
            h = headers.get(name.lower())
            return (row.get(h) or "").strip() if h else ""

        for line_no, row in enumerate(reader, start=2):
            set_code = get(row, "Set").lower()
            number = get(row, "Collector Number")
            if not set_code or not number:
                print(f"Skipping line {line_no}: missing Set or Collector Number")
                continue
            if skip_maybeboard and get(row, "maybeboard").lower() in ("true", "1", "yes"):
                skipped_maybe += 1
                continue
            rows.append({
                "set": set_code,
                "collector_number": number,
                "rarity": get(row, rarity_column) or "Unknown",
                "color": get(row, color_column) or "Unknown",
                "tagged_double_sided": "double-sided" in get(row, "tags").lower(),
                "line": line_no,
            })
    if skipped_maybe:
        print(f"Skipped {skipped_maybe} maybeboard row(s)")
    return rows


# --- Scryfall lookup ---------------------------------------------------------

def lookup_cards(session, rows, delay):
    """
    Ask Scryfall for card data in batches of 75 using POST /cards/collection.
    Returns a dict keyed by (set, collector_number) -> Scryfall card JSON,
    plus a list of identifiers Scryfall could not find.
    """
    found = {}
    not_found = []
    # De-duplicate so repeated rows (e.g. basic lands) are only looked up once.
    unique = list({(r["set"], r["collector_number"]): r for r in rows}.values())

    for i in range(0, len(unique), COLLECTION_BATCH_SIZE):
        batch = unique[i:i + COLLECTION_BATCH_SIZE]
        print(f"Looking up cards {i + 1}-{i + len(batch)} of {len(unique)}...", end="\r")
        body = {"identifiers": [
            {"set": r["set"], "collector_number": r["collector_number"]} for r in batch
        ]}
        res = request_with_retry(session, "POST", SCRYFALL_COLLECTION_URL, delay, json=body)
        data = res.json()
        for card in data.get("data", []):
            found[(card["set"].lower(), card["collector_number"])] = card
        for ident in data.get("not_found", []):
            not_found.append((ident.get("set", "").lower(), ident.get("collector_number", "")))
    print()
    return found, not_found


def card_faces(card):
    """
    Return a list of (face_label, png_url) for a card.
    Single-faced cards (and split/adventure cards, which share one image)
    have a top-level "image_uris". True double-faced cards (transform,
    modal DFC, etc.) have one "image_uris" per entry in "card_faces".
    """
    if "image_uris" in card:
        return [("front", card["image_uris"]["png"])]
    faces = []
    for idx, face in enumerate(card.get("card_faces", [])):
        if "image_uris" in face:
            faces.append(("front" if idx == 0 else "back", face["image_uris"]["png"]))
    return faces


# --- Output paths ------------------------------------------------------------

def target_path(args, row, face, sheet_counts):
    """
    Work out where a face image should be saved. In sheets mode the folder
    depends on how many images have already been saved successfully, so the
    caller must only bump `sheet_counts` after the download succeeds.
    """
    base = f"{row['set']}_{row['collector_number']}"
    suffix = "_back" if face == "back" else ""

    if args.mode == "sheets":
        key = "backs" if face == "back" else "fronts"
        folder_num = sheet_counts[key] // args.sheet_size + 1
        slot = sheet_counts[key] % args.sheet_size + 1          # 1-based position on the sheet
        folder = args.out / f"Sheet_{key}_{folder_num}"
        # Files are named by slot so a plain directory listing shows the sheet order.
        return folder / f"{slot:02d} {base}{suffix}.png"

    if args.mode == "rarity":
        folder = args.out / row["rarity"] / row["color"]
    else:
        folder = args.out / row["color"]
    return folder / f"{base}{suffix}.png"


def ensure_card_back(session, folder, delay, prepared_back):
    """
    Copy the card back into a sheet folder (once). `prepared_back` is the
    local file to copy from; if it is missing we fall back to fetching it
    from GitHub a single time so the script still works from a bare copy.
    """
    dest = folder / CARD_BACK_NAME
    if dest.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    if prepared_back is None or not prepared_back.exists():
        print(f"\n{CARD_BACK_NAME} not found next to the script - downloading it from GitHub.")
        download_file(session, CARD_BACK_FALLBACK_URL, dest, delay)
        return
    shutil.copy2(prepared_back, dest)   # copy2 keeps the original modification time


# --- Sheet assembly ----------------------------------------------------------

def grid_for(n_items, grid):
    """
    Choose the sheet grid for n_items tiles (cards + card back).
    'auto' picks the near-square layout that fits, capped at MAX_COLS x MAX_ROWS;
    a fixed (cols, rows) is used as given.
    """
    if grid != "auto":
        return grid
    cols = min(MAX_COLS, math.ceil(math.sqrt(n_items)))
    rows = math.ceil(n_items / cols)
    if rows > MAX_ROWS:                     # too many for a near-square grid: widen it
        cols = MAX_COLS
        rows = math.ceil(n_items / cols)
    return cols, min(rows, MAX_ROWS)


def load_manifest_index(out_dir):
    """Map manifest file path -> its manifest row, so sheets can name their slots."""
    index = {}
    manifest = out_dir / "manifest.csv"
    if manifest.exists():
        with open(manifest, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                index[row["file"]] = row      # later rows (re-downloads) override earlier ones
    return index


def sheet_slots(folder):
    """Return [(slot, path)] for the slot-numbered card files in a folder, in slot order."""
    slots = []
    for path in folder.iterdir():
        m = SLOT_FILE_RE.match(path.name)
        if m and path.name != CARD_BACK_NAME:
            slots.append((int(m.group(1)), path))
    return sorted(slots)


def assemble_sheet(folder, out_dir, sheets_dir, grid, tile_width, manifest_index):
    """
    Compose one Sheet_* folder into <sheets_dir>/<folder>.png and write
    <sheets_dir>/<folder>.csv with one row per slot (grid size, position, card).
    Cards are placed in slot order; the card back goes in the final slot of
    the grid (the slot Tabletop Simulator hides); anything between is left
    transparent. Returns the JSON summary.
    """
    from PIL import Image                   # imported here so the downloader works without Pillow

    slots = sheet_slots(folder)
    if not slots:
        return None
    back_path = folder / CARD_BACK_NAME
    n_items = len(slots) + (1 if back_path.exists() else 0)
    cols, rows = grid_for(n_items, grid)
    if n_items > cols * rows:
        raise ValueError(f"{folder.name}: {n_items} tiles do not fit a {cols}x{rows} grid")

    tile_w = tile_width
    tile_h = round(tile_width * CARD_H / CARD_W)
    sheet = Image.new("RGBA", (cols * tile_w, rows * tile_h), (0, 0, 0, 0))

    def place(path, index):
        with Image.open(path) as img:
            img = img.convert("RGBA")
            if img.size != (tile_w, tile_h):
                img = img.resize((tile_w, tile_h), Image.LANCZOS)
            sheet.paste(img, ((index % cols) * tile_w, (index // cols) * tile_h))

    def slot_row(index, path, info):
        return {
            "sheet": f"{folder.name}.png",
            "columns": cols, "rows": rows,
            "tile_width": tile_w, "tile_height": tile_h,
            "sheet_width": cols * tile_w, "sheet_height": rows * tile_h,
            "slot": index + 1,
            "column": index % cols + 1,
            "row": index // cols + 1,
            "file": path.name,
            "name": info.get("name", ""),
            "set": info.get("set", ""),
            "collector_number": info.get("collector_number", ""),
            "scryfall_id": info.get("scryfall_id", ""),
            "face": info.get("face", ""),
        }

    entries = []
    for i, (slot, path) in enumerate(slots):
        place(path, i)
        info = manifest_index.get(str(path.relative_to(out_dir)), {})
        entries.append(slot_row(i, path, info))
    back_slot = None
    if back_path.exists():
        back_slot = cols * rows                 # last slot, 1-based
        place(back_path, back_slot - 1)
        entries.append(slot_row(back_slot - 1, back_path, {"name": "Card back", "face": "back"}))

    sheets_dir.mkdir(parents=True, exist_ok=True)
    png_path = sheets_dir / f"{folder.name}.png"
    sheet.save(png_path, "PNG", optimize=True)

    csv_path = sheets_dir / f"{folder.name}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(entries)

    return {
        "sheet": png_path.name,
        "source_folder": folder.name,
        "columns": cols,
        "rows": rows,
        "cards": len(slots),
        "back_slot": back_slot if back_slot is not None else "",
        "tile_width": tile_w,
        "tile_height": tile_h,
        "sheet_width": cols * tile_w,
        "sheet_height": rows * tile_h,
        "manifest": csv_path.name,
    }


def assemble_sheets(args):
    """
    Build every Sheet_* folder under --out into <out>/cardsheets and write
    cardsheets/sheets.csv, one row per sheet. Returns the number built.
    """
    folders = sorted(
        (p for p in args.out.iterdir() if p.is_dir() and p.name.startswith("Sheet_")),
        key=lambda p: (p.name.rsplit("_", 1)[0], int(p.name.rsplit("_", 1)[1])),
    )
    if not folders:
        print("No Sheet_* folders found to assemble.")
        return 0
    manifest_index = load_manifest_index(args.out)
    sheets_dir = args.out / "cardsheets"
    summaries = []
    for folder in folders:
        summary = assemble_sheet(folder, args.out, sheets_dir, args.grid, args.tile_width, manifest_index)
        if summary is None:
            print(f"{folder.name}: no card files, skipped")
            continue
        summaries.append(summary)
        print(f"{folder.name}: {summary['columns']}x{summary['rows']} grid, "
              f"{summary['cards']} cards + back -> cardsheets/{summary['sheet']} "
              f"({summary['sheet_width']}x{summary['sheet_height']} px)")
    if summaries:
        with open(sheets_dir / "sheets.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHEET_INDEX_FIELDS)
            writer.writeheader()
            writer.writerows(summaries)
    return len(summaries)


# --- Main --------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)
    if args.assemble_only:
        if not args.out.is_dir():
            sys.exit(f"Error: {args.out} not found.")
        return 0 if assemble_sheets(args) else 1

    if not args.csv.is_file():
        sys.exit(f"Error: {args.csv} not found.")
    args.out.mkdir(parents=True, exist_ok=True)

    rows = read_cube_csv(args.csv, args.color_column, args.rarity_column, args.skip_maybeboard)
    if not rows:
        sys.exit("Error: no usable rows found in the CSV.")
    print(f"Read {len(rows)} card rows from {args.csv}")

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        cards, not_found = lookup_cards(session, rows, args.delay)
    except RuntimeError as e:
        sys.exit(f"Error: could not reach Scryfall. {e}")
    for set_code, number in not_found:
        print(f"Not found on Scryfall: {set_code}/{number}")

    local_back = Path(__file__).resolve().parent / CARD_BACK_NAME
    sheet_counts = {"fronts": 0, "backs": 0}
    downloaded, skipped, failed = 0, 0, []
    manifest_rows = []

    for row in rows:
        card = cards.get((row["set"], row["collector_number"]))
        if card is None:
            failed.append((row, "not found on Scryfall"))
            continue

        faces = card_faces(card)
        if row["tagged_double_sided"] and len(faces) < 2:
            print(f"\nWarning: {card['name']} is tagged double-sided but Scryfall has only one face.")

        for face, url in faces:
            dest = target_path(args, row, face, sheet_counts)

            # In sheets mode, drop the card back into each new folder.
            if args.mode == "sheets" and not args.no_card_back:
                ensure_card_back(session, dest.parent, args.delay, local_back)

            if dest.exists() and not args.force:
                skipped += 1
                if args.mode == "sheets":
                    sheet_counts["backs" if face == "back" else "fronts"] += 1
                continue

            print(f"Downloading: {dest.name}        ", end="\r")
            try:
                sha = download_file(session, url, dest, args.delay)
            except Exception as e:
                print(f"\nError downloading {card['name']} ({face}): {e}")
                failed.append((row, f"{face}: {e}"))
                continue

            downloaded += 1
            if args.mode == "sheets":
                # Only count a slot as used once the file is really on disk.
                sheet_counts["backs" if face == "back" else "fronts"] += 1
            manifest_rows.append({
                "file": str(dest.relative_to(args.out)),
                "name": card["name"],
                "set": card["set"],
                "collector_number": card["collector_number"],
                "scryfall_id": card["id"],
                "face": face,
                "source_url": url,
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sha256": sha,
            })

    # Append this run's downloads to the manifest (header only when the file is new).
    if manifest_rows:
        manifest = args.out / "manifest.csv"
        new_file = not manifest.exists()
        with open(manifest, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerows(manifest_rows)

    print(f"\nDone. {downloaded} downloaded, {skipped} already present, {len(failed)} failed.")
    if failed:
        print("Failed cards:")
        for row, reason in failed:
            print(f"  line {row['line']}: {row['set']}/{row['collector_number']} - {reason}")

    if args.assemble:
        print()
        assemble_sheets(args)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
