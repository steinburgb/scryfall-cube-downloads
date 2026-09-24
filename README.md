# scryfall-cube-downloads

A Python script that downloads Magic: The Gathering card images from the [Scryfall API](https://scryfall.com/docs/api) for a cube list in CSV form (for example a CubeCobra export). Images are saved as PNGs into a folder layout of your choice, double-faced cards are detected automatically, and every download is recorded in a manifest with its checksum.

---

## Requirements

* Python 3.8 or newer (any current version works; tested on 3.10 and 3.14)
* `requests`, plus `Pillow` if you use `--assemble`: `pip install -r requirements.txt`

---

## Usage

```
python scryfall_api_cube_images.py cube.csv
python scryfall_api_cube_images.py cube.csv --out ./images --mode rarity
python scryfall_api_cube_images.py cube.csv --out ./sheets --mode sheets --assemble
python scryfall_api_cube_images.py cube.csv --out ./sheets --mode sheets --assemble-only
python scryfall_api_cube_images.py --help
```

| Option | Description | Default |
| :--- | :--- | :--- |
| `csv` | Path to the input CSV. | (required) |
| `--out DIR` | Folder to save images into. | `~/Downloads/Scryfall_Images` |
| `--mode colour\|rarity\|sheets` | Folder layout (see below). | `colour` |
| `--sheet-size N` | Cards per folder in `sheets` mode. | `69` |
| `--color-column NAME` | CSV column used for colour folder names. | `Color` |
| `--rarity-column NAME` | CSV column used for rarity folder names. | `Rarity` |
| `--delay SECONDS` | Pause between requests. | `0.1` |
| `--skip-maybeboard` | Skip rows whose `maybeboard` column is `true`. | off |
| `--force` | Re-download files that already exist. | off |
| `--no-card-back` | In `sheets` mode, don't copy the card back into each folder. | off |
| `--assemble` | After downloading in `sheets` mode, compose each folder into one grid image. | off |
| `--assemble-only` | Compose sheets from folders already in `--out`; download nothing. | off |
| `--grid auto\|COLSxROWS` | Sheet grid: near-square (max 10×7) or fixed, e.g. `10x7`. | `auto` |
| `--tile-width N` | Width in pixels of each card on the sheet; height follows the card ratio. `745` is Scryfall's native size. | `488` |

Paths can be given exactly as they appear on your system; there is no need to change `\` to `/` on Windows.

---

## Folder layouts

**`colour`** (default): `<out>/<Color>/<set>_<number>.png`

**`rarity`**: `<out>/<Rarity>/<Color>/<set>_<number>.png`. Intended for set cubes where rarity matters when building packs.

**`sheets`**: prepares files for card-sheet creation for Tabletop Simulator. Colour and rarity are ignored.
* Images are grouped into folders of 69 cards each, in CSV order.
* Fronts and backs go into separate folders (`Sheet_fronts_1`, `Sheet_backs_1`, ...).
* Files are named by their slot on the sheet (`01 thb_1.png`, `02 thb_2.png`, ...), so a directory listing shows the sheet order.
* A copy of the card back (`00 Back.png`, 745×1040 to match Scryfall's PNGs) is placed in every folder.

**Assembling sheets.** With `--assemble` (or `--assemble-only` to rebuild from folders already downloaded), each `Sheet_*` folder is composed into one image in `<out>/cardsheets/`:
* Cards are placed in slot order, left to right, top to bottom. The card back goes in the last slot of the grid, which Tabletop Simulator treats as the hidden back; any slots between are transparent.
* `--grid auto` picks the smallest near-square grid that fits (a full 69-card folder is 10×7; a 42-card remainder is 7×7). `--grid 10x7` makes every sheet the same size.
* Each card is drawn at `--tile-width` pixels wide (default 488, giving 4880×4767 for a full 10×7 sheet). This is a resolution setting, not compression: sheets are always lossless PNGs, and the source card images are untouched. Smaller tiles mean smaller textures for Tabletop Simulator to load; `--tile-width 745` keeps Scryfall's native size (7450×7280 per full sheet). Rebuild at another width any time with `--assemble-only --tile-width N`.
* Two CSV manifests are written to `cardsheets/`:
  * `sheets.csv`: one row per sheet with columns, rows (the numbers TTS asks for on import), card count, back slot and pixel size.
  * `<sheet>.csv` (e.g. `Sheet_fronts_1.csv`): one row per slot with its position (slot, column, row), the source file, card name, set, collector number, Scryfall ID and face. The last row is the card back. Sheet-level values repeat on every row so the file stands alone in a spreadsheet.

Back faces of double-faced cards are saved as `<set>_<number>_back.png`.

---

## CSV requirements

Only two columns are required (header names are not case-sensitive):

* **Set:** the set code (e.g. `thb`, `war`).
* **Collector Number:** the card's number within the set.

Optional columns:

* **Color** and **Rarity:** used for folder names in `colour` and `rarity` modes. Missing or empty values fall back to `Unknown`. Use `--color-column` / `--rarity-column` if your export names them differently (e.g. `--color-column "Color Category"`).
* **maybeboard:** CubeCobra exports include maybeboard cards with this column set to `true`. Pass `--skip-maybeboard` to leave them out.
* **tags:** no longer needed. Double-faced cards are detected from Scryfall's data. If a card is tagged `double-sided` but Scryfall reports only one face, the script prints a warning so you can check the row.

A CubeCobra CSV export works as-is; see `example.csv` for the expected shape.

---

## Running the tests

The tests run offline against a fake Scryfall, using a real CubeCobra export in `tests/data/`:

```
python -m unittest discover -s tests -v
```

Set `SCRYFALL_LIVE=1` to also run one small download of five cards against the real API.

---

## How it works

1. **Read the CSV.** Rows without a set code or collector number are skipped with a message.
2. **Look up cards.** Set and collector-number pairs are sent to Scryfall's `/cards/collection` endpoint in batches of 75. This returns each card's name, Scryfall ID and image links, and tells the script whether the card has a separate back face.
3. **Download images.** Each face is fetched from the image link Scryfall returned. Downloads are written to a temporary `.part` file and renamed on success, so an interrupted run never leaves a broken image. Files that already exist are skipped, so a run can be resumed.
4. **Handle errors.** Requests time out after 30 seconds. Rate-limit (429) and server errors are retried with a backoff, honouring Scryfall's `Retry-After` header. A pause of 100 ms is kept between requests, including after failures, per Scryfall's guidelines.
5. **Write the manifest.** `manifest.csv` in the output folder gets one row per downloaded file: file path, card name, set, collector number, Scryfall ID, face, source URL, download time (UTC) and SHA-256 checksum. Rows are appended on each run.
6. **Report.** A summary of downloaded, skipped and failed cards is printed at the end, with the CSV line number of each failure. The exit code is `1` if anything failed.
