import os
import requests
import time
import csv

# --- Configuration ---
DOWNLOAD_PATH = os.path.join(os.path.expanduser("~"), "Downloads", "Scryfall_Images")
# It's recommended to copy and paste the entire filepath for your CSV file for precision. If on Windows, the '\' characters in this filepath will need to be replaced with '/' before running the script to prevent errors.
CSV_FILE = "data.csv" 

# Set this to True to enable Rarity folders (Rarity -> Color -> Image). This option is useful when creating a set cube where rarities need to be separated out from one another to construct packs.
# Set this to False to sort only by Color (Color -> Image). This option is enabled by default and is suitable for any non-set cube.
SORT_BY_RARITY = False 

HEADERS = {
    "User-Agent": "CubeAssetDownloader/1.0",
    "Accept": "image/png"
}

def download_images():
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)
        print(f"Created base folder: {DOWNLOAD_PATH}")

    try:
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            print(f"Starting downloads (Sort by Rarity: {SORT_BY_RARITY})...\n")

            for row in reader:
                # 1. Extraction
                set_code = row.get("Set", "").strip().lower()
                collector_num = row.get("Collector Number", "").strip()
                rarity = row.get("Rarity", "Unknown").strip()
                color = row.get("Color", "Unknown").strip()
                tags = row.get("tags", "").strip().lower()

                if not set_code or not collector_num:
                    continue

                # 2. Determine Folder Path based on Flag
                if SORT_BY_RARITY:
                    target_dir = os.path.join(DOWNLOAD_PATH, rarity, color)
                else:
                    target_dir = os.path.join(DOWNLOAD_PATH, color)

                # 3. Define the downloads for this row
                download_tasks = [{"suffix": "", "url_params": "&face=front"}]
                # This flag checks for the specific tag 'double-sided' to know when to look for the back side of a card. If you use a different tag, simply change it to the exact wording of your tag in the line below.
                if "double-sided" in tags:
                    download_tasks.append({"suffix": "_back", "url_params": "&face=back"})

                # 4. Execute downloads
                for task in download_tasks:
                    url = f"https://api.scryfall.com/cards/{set_code}/{collector_num}?format=image&version=png{task['url_params']}"
                    
                    try:
                        if not os.path.exists(target_dir):
                            os.makedirs(target_dir)

                        filename = f"{set_code}_{collector_num}{task['suffix']}.png"
                        file_path = os.path.join(target_dir, filename)

                        # Clean output display
                        display_path = f"{rarity}/{color}" if SORT_BY_RARITY else color
                        print(f"Downloading to {display_path}: {filename}    ", end="\r")
                        
                        response = requests.get(url, headers=HEADERS, stream=True)
                        response.raise_for_status()

                        with open(file_path, "wb") as img_file:
                            for chunk in response.iter_content(1024):
                                img_file.write(chunk)

                        time.sleep(0.1) # Scryfall rate limit

                    except Exception as e:
                        print(f"\nError downloading {set_code}/{collector_num}: {e}")

    except FileNotFoundError:
        print(f"Error: {CSV_FILE} not found.")
        return

    print(f"\n\nSuccess! Your images are organized in: {DOWNLOAD_PATH}")

if __name__ == "__main__":
    download_images()
