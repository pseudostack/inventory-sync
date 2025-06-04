from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import tempfile
import os
import time
import platform
from ftplib import FTP
import pandas as pd
import requests

# --- Configuration ---
system_name = platform.system()

DEALERPULL_LOGIN_URL = "https://app.dealerpull.com/login"
INVENTORY_PAGE_URL = "https://app.dealerpull.com/inventory-list"
EXPORTED_FILENAME = "inventory_export.csv"
FINAL_FILENAME = "inventory.csv"

FTP_HOST = "ftp.eddysauto.ca"
FTP_USER = "berlinautosales.ca@berlinautosales.ca"
FTP_PASS = "B2010luetooth5!"
FTP_TARGET_PATH = "inventory.csv"

LOGIN_EMAIL = "farhad@berlinautosales.ca"
LOGIN_PASS = "B2010luetooth5!"

CARFAX_FOLDER = "static/carfax"
os.makedirs(CARFAX_FOLDER, exist_ok=True)

if system_name == "Darwin":
    DOWNLOAD_DIR = os.path.expanduser("~/Downloads")
elif system_name == "Linux":
    DOWNLOAD_DIR = os.path.abspath(os.path.join(os.getcwd(), "..", "downloads"))
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
else:
    raise Exception(f"Unsupported OS: {system_name}")

print(f"📁 Using download dir: {DOWNLOAD_DIR}")

# --- Setup Chrome ---
chrome_options = Options()
prefs = {
    "download.default_directory": DOWNLOAD_DIR,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
}
chrome_options.add_experimental_option("prefs", prefs)
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--headless=new')
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
driver = webdriver.Chrome(options=chrome_options)

try:
    # --- Login ---
    driver.get(DEALERPULL_LOGIN_URL)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(LOGIN_EMAIL)
    driver.find_element(By.NAME, "password").send_keys(LOGIN_PASS + Keys.RETURN)
    print("Logging in...")
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[ui-view='root']")))
    time.sleep(3)

    # --- Go to inventory ---
    driver.get(INVENTORY_PAGE_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "select-all")))
    time.sleep(1)

    # --- Open field selector dropdown ---
    dropdown_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button#colDropdown"))
    )
    dropdown_button.click()
    time.sleep(1)

    # --- Check required fields ---
    fields_to_check = ["description", "trim", "vehicle type", "drive", "transmission",
                       "cylinders", "colour", "odometer", "List price", "salePrice", "images", "links"]
    checkboxes = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show input[type='checkbox']")
    labels = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show .custom-control-label")

    for i, label in enumerate(labels):
        field_name = label.text.strip().lower()
        if field_name in [f.lower() for f in fields_to_check]:
            box = checkboxes[i]
            is_checked = box.get_attribute("ng-reflect-model") == "true"
            if not is_checked:
                driver.execute_script("arguments[0].click();", box)
                print(f"✅ Enabled export field: {field_name}")

    # --- Verify 'links' is enabled ---
    links_index = next((i for i, label in enumerate(labels) if label.text.strip().lower() == 'links'), None)
    if links_index is not None:
        links_checkbox = checkboxes[links_index]
        links_checked = links_checkbox.get_attribute("ng-reflect-model") == "true"
        print(f"🔍 Links checkbox is {'ENABLED' if links_checked else 'DISABLED'} after processing.")
    else:
        print("⚠️ Links checkbox not found!")

    time.sleep(2)

    # --- Set page size to 100 ---
    page_size_dropdown = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "select[data-cy='page-count']"))
    )
    driver.execute_script("arguments[0].value = '100'; arguments[0].dispatchEvent(new Event('change'));", page_size_dropdown)
    time.sleep(3)

    # --- Select all vehicles ---
    select_all = driver.find_element(By.ID, "select-all")
    driver.execute_script("""
        arguments[0].checked = true;
        arguments[0].setAttribute('ng-reflect-model', 'true');
        arguments[0].dispatchEvent(new Event('input'));
        arguments[0].dispatchEvent(new Event('change'));
    """, select_all)
    time.sleep(2)

    # --- Export inventory ---
    export_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
    )
    export_button.click()
    print("Exporting inventory...")
    time.sleep(10)

    # --- Move and rename file ---
    downloaded_path = os.path.join(DOWNLOAD_DIR, EXPORTED_FILENAME)
    final_path = os.path.join(DOWNLOAD_DIR, FINAL_FILENAME)
    if os.path.exists(final_path):
        os.remove(final_path)
    if os.path.exists(downloaded_path):
        os.rename(downloaded_path, final_path)
        print(f"Renamed to: {final_path}")
    else:
        raise FileNotFoundError(f"{EXPORTED_FILENAME} not found in {DOWNLOAD_DIR}")

    # --- Download Carfax PDFs ---
    df = pd.read_csv(final_path)
    for idx, row in df.iterrows():
        vin = str(row['VIN'])
        link = row.get('Links', '').strip()
        last6 = vin[-6:]
        filename = f"{last6}_carfax.pdf"
        local_path = os.path.join(CARFAX_FOLDER, filename)

        if link:
            try:
                response = requests.get(link, timeout=15)
                if response.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(response.content)
                    print(f"✅ Downloaded Carfax for VIN {vin} -> {local_path}")
                else:
                    print(f"❌ Failed to download for VIN {vin}, status: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Error downloading for VIN {vin}: {e}")
        else:
            print(f"🔍 No link for VIN {vin}. Will rely on local upload.")

    # --- Upload CSV via FTP ---
    print("Uploading to FTP...")
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        with open(final_path, 'rb') as f:
            ftp.storbinary(f"STOR {FTP_TARGET_PATH}", f)
    print("✅ Upload complete.")

finally:
    driver.quit()
