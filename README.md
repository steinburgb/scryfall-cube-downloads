# scryfall-cube-downloads
This Python script automates the downloading of Magic: The Gathering card art from the Scryfall API based on a local CSV file. It organizes images into a structured folder hierarchy and handles double-sided cards automatically.

---

### **Core Functionality**
* **CSV-Driven Downloads:** Reads card data (Set, Collector Number, Rarity, Color, and Tags) from a source CSV file.
* **Dynamic URL Generation:** Constructs Scryfall API requests using the format:  
    `https://api.scryfall.com/cards/{set}/{number}?format=image&version=png`
* **Automated Organization:** Saves images into folders named after the card's **Color** (and optionally **Rarity**).
* **Double-Sided Support:** Scans the "Tags" column for the string `double-sided`. if found, it downloads both the front and back faces of the card.
* **Rate Limit Compliance:** Includes a **100ms delay** between requests to respect Scryfall’s API guidelines.

---

### **Configuration Options**
Adjust these variables at the top of the script to change its behavior:

| Variable | Description | Default Value |
| :--- | :--- | :--- |
| `DOWNLOAD_PATH` | The directory where images will be saved. | `~/Downloads/Scryfall_Images` |
| `CSV_FILE` | The name of the input data file. | `"data.csv"` |
| `SORT_BY_RARITY` | **Flag:** Set `True` to nest colors inside rarity folders; `False` for color folders only. | `False` |

---

### **CSV Requirements**
The script expects a CSV file with the following header names (case-sensitive):
* **Set:** The 3-4 letter set code (e.g., `thb`, `war`).
* **Collector Number:** The card number within the set.
* **Rarity:** Used for folder naming (e.g., `Rare`, `Mythic`).
* **Color:** Used for folder naming (e.g., `Blue`, `Red`, `Multicolor`).
* **Tags:** Looked at for the `double-sided` keyword.

---

### **Execution Flow**
1.  **Initialization:** Creates the base download folder if it doesn't exist.
2.  **Row Processing:** Iterates through each row of the CSV.
3.  **Path Resolution:** Determines the target folder based on the `SORT_BY_RARITY` flag.
4.  **Download Tasks:**
    * Always queues the **Front** face.
    * Queues the **Back** face if "double-sided" is detected in the Tags.
5.  **File Saving:** Saves files as `{set}_{number}.png` (and `{set}_{number}_back.png` for reverse sides).
6.  **Error Handling:** Catches and reports network errors or missing files without stopping the entire batch.
