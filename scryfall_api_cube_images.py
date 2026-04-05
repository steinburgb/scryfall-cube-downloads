import os
import requests
import time
import csv
import shutil

# --- Configuration ---
DOWNLOAD_PATH = os.path.join(os.path.expanduser("~"), "Downloads", "Scryfall_Images")
CSV_FILE = "data.csv" # Use the full filepath here for specificity. If in Windows, replace all \ characters in the filepath with / to avoid errors.
GITHUB_ASSET_URL = "https://raw.githubusercontent.com/steinburgb/scryfall-cube-downloads/refs/heads/main/00 Back.png"

# --- Flags ---
SORT_BY_RARITY = False # If False, sorts by colour only. If true, sorts by rarity first, then colour. This option is intended for set cubes where rarity matters for constructing packs.
CARDSHEET_CREATION_OPTIMIZED = True  # Set to True for unsorted sequential 69-card batches, ignoring sort preferences detailed above. This prepares cards directly for card sheet creation.

HEADERS = {
    "User-Agent": "MTGCubeImageDownloader/1.0",
    "Accept": "image/png"
}

def download_asset(target_dir):
    # Downloads the GitHub asset into the specified folder.
    asset_name = GITHUB_ASSET_URL.split("/")[-1]
    path = os.path.join(target_dir, asset_name)
    if not os.path.exists(path):
        try:
            r = requests.get(GITHUB_ASSET_URL, stream=True)
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
        except Exception as e:
            print(f"\nError downloading GitHub asset: {e}")

def get_target_path(row, is_back, counts):
    # Determines the folder and filename based on active flags.
    set_code = row.get("Set", "").strip().lower()
    num = row.get("Collector Number", "").strip()
    
    if CARDSHEET_CREATION_OPTIMIZED:
        face_key = "backs" if is_back else "fronts"
        # Calculate folder number (1-indexed)
        folder_num = (counts[face_key] // 69) + 1
        
        target_dir = os.path.join(DOWNLOAD_PATH, f"Sheet_{face_key}_{folder_num}")
        filename = f"01x {set_code}_{num}{'_back' if is_back else ''}.png"
        
        # Trigger GitHub asset download if it's a new folder
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
            download_asset(target_dir)
            
        counts[face_key] += 1
        return target_dir, filename
    else:
        # Standard Mode
        rarity = row.get("Rarity", "Unknown").strip()
        color = row.get("Color", "Unknown").strip()
        target_dir = os.path.join(DOWNLOAD_PATH, rarity, color) if SORT_BY_RARITY else os.path.join(DOWNLOAD_PATH, color)
        filename = f"{set_code}_{num}{'_back' if is_back else ''}.png"
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        return target_dir, filename

def download_images():
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)

    # State tracking for Cardsheet mode
    counts = {"fronts": 0, "backs": 0}

    try:
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                set_code = row.get("Set", "").strip().lower()
                collector_num = row.get("Collector Number", "").strip()
                tags = row.get("Tags", "").strip().lower()

                if not set_code or not collector_num:
                    continue

                # Define tasks
                tasks = [False] # Front
                if "double-sided" in tags:
                    tasks.append(True) # Back

                for is_back in tasks:
                    target_dir, filename = get_target_path(row, is_back, counts)
                    file_path = os.path.join(target_dir, filename)
                    
                    face_param = "&face=back" if is_back else "&face=front"
                    url = f"https://api.scryfall.com/cards/{set_code}/{collector_num}?format=image&version=png{face_param}"

                    try:
                        print(f"Processing: {filename}        ", end="\r")
                        res = requests.get(url, headers=HEADERS, stream=True)
                        res.raise_for_status()

                        with open(file_path, "wb") as img:
                            for chunk in res.iter_content(1024):
                                img.write(chunk)
                        time.sleep(0.1)
                    except Exception as e:
                        print(f"\nError: {e}")

    except FileNotFoundError:
        print(f"Error: {CSV_FILE} not found.")

if __name__ == "__main__":
    download_images()
