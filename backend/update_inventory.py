
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import tempfile
import os
import time
import platform
import requests
import re
import glob


import pandas as pd
from pathlib import Path
from ftplib import FTP
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv("/root/inventory-sync/backend/.env")





OPENLANE_URL = "https://app.openlane.ca"
CARFAX_DIR = Path("/root/inventory-sync/backend/static/carfax")  # adjust if needed
CARFAX_DIR.mkdir(parents=True, exist_ok=True)

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


FLASK_CARFAX_DIR = "/root/inventory-sync/backend/static/carfax"  # adjust if different
os.makedirs(FLASK_CARFAX_DIR, exist_ok=True)

def newest_file(pattern: str):
    files = glob.glob(pattern)
    return max(files, key=os.path.getmtime) if files else None

def wait_for_new_pdf(download_dir: str, timeout: int = 60):
    """
    Waits for a new PDF to appear in download_dir and finish downloading.
    """
    start = time.time()
    before = set(glob.glob(os.path.join(download_dir, "*.pdf")))

    while time.time() - start < timeout:
        # Chrome uses .crdownload while downloading
        if glob.glob(os.path.join(download_dir, "*.crdownload")):
            time.sleep(0.5)
            continue

        after = set(glob.glob(os.path.join(download_dir, "*.pdf")))
        new_files = list(after - before)
        if new_files:
            # return newest of the newly created
            return max(new_files, key=os.path.getmtime)

        time.sleep(0.5)

    return None

def safe_switch_to_new_tab(driver, old_handles, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        handles = driver.window_handles
        if len(handles) > len(old_handles):
            new_handle = [h for h in handles if h not in old_handles][0]
            driver.switch_to.window(new_handle)
            return True
        time.sleep(0.25)
    return False

def carfax_filename(vin: str, mode: str = "last4") -> str:
    vin = (vin or "").strip()
    if mode == "first4":
        key = vin[:4]
    elif mode == "full":
        key = vin
    else:
        key = vin[-4:]
    return f"{key}_carfax.pdf"

def download_file(url: str, out_path: str):
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)

def last4(vin: str) -> str:
    vin = (vin or "").strip()
    return vin[-4:] if len(vin) >= 4 else vin

def carfax_path_for_vin(vin: str) -> Path:
    return CARFAX_DIR / f"{last4(vin)}_carfax.pdf"

def normalize_vin(v) -> str:
    s = str(v).strip()
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s

def login_openlane(driver, wait):
    driver.get(OPENLANE_URL)

    user_el = wait.until(EC.presence_of_element_located((By.ID, "idp-discovery-username")))
    user_el.clear()
    user_el.send_keys(OPENLANE_USER)

    next_btn = wait.until(EC.element_to_be_clickable((By.ID, "idp-discovery-submit")))
    next_btn.click()

    pwd_el = wait.until(EC.presence_of_element_located((By.ID, "okta-signin-password")))
    pwd_el.clear()
    pwd_el.send_keys(OPENLANE_PASS)

    try:
        signin = wait.until(EC.element_to_be_clickable((By.ID, "okta-signin-submit")))
        signin.click()
    except TimeoutException:
        driver.execute_script("""
            const btn = document.querySelector('input[type="submit"],button[type="submit"]');
            if (btn) btn.click();
        """)

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
    except TimeoutException:
        pass

def download_carfax_for_vin(driver, wait, vin: str, download_dir: str, carfax_dir: str, name_mode="last4") -> bool:
    vin = (vin or "").strip().upper()
    if not vin:
        return False

    def make_name(v: str):
        if name_mode == "first4":
            key = v[:4]
        elif name_mode == "full":
            key = v
        else:
            key = v[-4:]
        return f"{key}_carfax.pdf"

    target_path = os.path.join(carfax_dir, make_name(vin))
    os.makedirs(carfax_dir, exist_ok=True)

    # If already downloaded, skip
    if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        return True

    # 1) Go to purchases
    driver.get(OPENLANE_PURCHASES_URL)

    # 2) Click "Order History" segment button by its label
    # Your snippet: ignite-typography ... data-label="Order History"
    try:
        order_history = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//*[@data-label='Order History' or normalize-space()='Order History' or contains(.,'Order History')]"
        )))
        driver.execute_script("arguments[0].click();", order_history)
    except TimeoutException:
        # sometimes you land there already; ignore
        pass

    # 3) Find the list-item card with this VIN and click it
    # Your HTML shows: <div class="list-item ..."><div class="vin ...">VIN</div>
    card_xpath = (
        "//div[contains(@class,'list-item') and "
        ".//div[contains(@class,'vin') and normalize-space()=$VIN]]"
    )

    # Selenium doesn't support $VIN param directly; we’ll format safely:
    card = wait.until(EC.element_to_be_clickable((
        By.XPATH,
        f"//div[contains(@class,'list-item') and .//div[contains(@class,'vin') and normalize-space()='{vin}']]"
    )))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card)
    driver.execute_script("arguments[0].click();", card)

    # 4) Click "Condition report" (opens a new tab)
    old_handles = driver.window_handles[:]

    condition = wait.until(EC.element_to_be_clickable((
        By.XPATH,
        "//*[contains(.,'Condition report') and (self::div or self::span or self::a or self::button)]"
    )))
    driver.execute_script("arguments[0].click();", condition)

    # Switch to new tab
    if not safe_switch_to_new_tab(driver, old_handles, timeout=10):
        # sometimes it opens in same tab
        pass

    # 5) Grab the CARFAX link href (ignite-link with href to vhr.carfax.ca)
    carfax_link = wait.until(EC.presence_of_element_located((
        By.CSS_SELECTOR,
        "ignite-link[href*='vhr.carfax.ca']"
    )))
    carfax_url = carfax_link.get_attribute("href")
    if not carfax_url:
        return False

    # 6) Open the carfax URL (this should trigger a PDF download in many setups)
    # If it opens a new tab, we still just navigate directly.
    driver.get(carfax_url)

    # 7) Wait for PDF download to complete
    pdf_path = wait_for_new_pdf(download_dir, timeout=90)
    if not pdf_path:
        # Sometimes the Carfax link opens a page with a download button.
        # If that happens, inspect that page and add a click here.
        return False

    # 8) Move + rename
    if os.path.exists(target_path):
        os.remove(target_path)
    os.rename(pdf_path, target_path)
    return True

if system_name == "Darwin":  # macOS
    DOWNLOAD_DIR = os.path.expanduser("~/Downloads")
elif system_name == "Linux":  # Ubuntu or other Linux
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
    "safebrowsing.enabled": True,
    "plugins.always_open_pdf_externally": True,  # ✅ force download instead of Chrome PDF viewer
}

chrome_options.add_experimental_option("prefs", prefs)
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--headless=new')  # 'new' ensures compatibility with recent Chrome


chrome_options.add_argument("--disable-gpu")  # good practice for Windows
chrome_options.add_argument("--window-size=1920,1080")  # optional, can help with layout
chrome_options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
driver = webdriver.Chrome(options=chrome_options)

try:
    # Step 1: Log in
    driver.get(DEALERPULL_LOGIN_URL)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(LOGIN_EMAIL)
    driver.find_element(By.NAME, "password").send_keys(LOGIN_PASS + Keys.RETURN)

    print("Logging in...")
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[ui-view='root']")))
    time.sleep(3)

    # Step 2: Navigate to inventory page
    driver.get(INVENTORY_PAGE_URL)
    print("Waiting for inventory list to load...")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "select-all")))
    time.sleep(1)

    # Step 3: Open the field selector dropdown
    print("Opening field selector dropdown...")
    dropdown_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button#colDropdown"))
    )
    dropdown_button.click()
    time.sleep(1)

    # Step 4: Ensure required fields are checked
    fields_to_check = [
        "vin", "description", "trim", "vehicle type", "drive", "transmission",
        "cylinders", "colour", "odometer", "List price", "salePrice", "images"
    ]

    checkboxes = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show input[type='checkbox']")
    labels = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show .custom-control-label")

    for i, label in enumerate(labels):
        if label.text.strip().lower() in [f.lower() for f in fields_to_check]:
            box = checkboxes[i]
            is_checked = box.get_attribute("ng-reflect-model") == "true"
            if not is_checked:
                driver.execute_script("arguments[0].click();", box)
                print(f"✅ Enabled export field: {label.text.strip()}")

    time.sleep(2)

    
    print("Changing page size to 100...")

    wait = WebDriverWait(driver, 20)

    def set_mat_select_by_text(data_cy: str, text: str):
        # open the mat-select
        trigger = wait.until(EC.element_to_be_clickable((
            By.CSS_SELECTOR, f"mat-select[data-cy='{data_cy}'] .mat-select-trigger"
        )))
        driver.execute_script("arguments[0].click();", trigger)

        # pick the option from the overlay
        option = wait.until(EC.element_to_be_clickable((
            By.XPATH, f"//mat-option//span[normalize-space()='{text}']"
        )))
        driver.execute_script("arguments[0].click();", option)

        # confirm the selection shows in the trigger
        wait.until(lambda d: text in d.find_element(
            By.CSS_SELECTOR, f"mat-select[data-cy='{data_cy}']"
        ).text)

    set_mat_select_by_text("page-count", "100")

    # Wait for the page to refresh with 100 cars
    time.sleep(3)

    # Step 5: Click 'Select All' checkbox    
    print("Clicking select-all checkbox...")
    select_all = driver.find_element(By.ID, "select-all")
    driver.execute_script("""
        arguments[0].checked = true;
        arguments[0].setAttribute('ng-reflect-model', 'true');
        arguments[0].dispatchEvent(new Event('input'));
        arguments[0].dispatchEvent(new Event('change'));
    """, select_all)

    time.sleep(2)

    # Step 6: Click Export button
    export_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
    )
    export_button.click()
    print("Exporting inventory...")

    # Step 7: Wait for download
    time.sleep(10)

    # Step 8: Rename and upload
    downloaded_path = os.path.join(DOWNLOAD_DIR, EXPORTED_FILENAME)
    final_path = os.path.join(DOWNLOAD_DIR, FINAL_FILENAME)

    if os.path.exists(final_path):
        os.remove(final_path)

    CARFAX_DIR = "/root/inventory-sync/backend/static/carfax"
    os.makedirs(CARFAX_DIR, exist_ok=True)

    if os.path.exists(downloaded_path):
        os.rename(downloaded_path, final_path)
        print(f"Renamed to: {final_path}")

        # ✅ NEW: read VINs + download missing carfax PDFs
        df = pd.read_csv(final_path, engine="python", on_bad_lines="skip")

        # normalize VIN column name
        if "VIN" not in df.columns and "vin" in df.columns:
            df = df.rename(columns={"vin": "VIN"})

        vins = [str(v).strip().upper() for v in df["VIN"].dropna().tolist()]

        wait = WebDriverWait(driver, 25)
        login_openlane(driver, wait)   # login ONCE

        for vin in vins:
            target_pdf = os.path.join(CARFAX_DIR, f"{vin[-4:]}_carfax.pdf")
            if not os.path.exists(target_pdf):
                ok = download_carfax_for_vin(driver, wait, vin, DOWNLOAD_DIR, CARFAX_DIR, name_mode="last4")
                print(vin, "carfax:", "OK" if ok else "FAILED")

    else:
        raise FileNotFoundError(f"{EXPORTED_FILENAME} not found in {DOWNLOAD_DIR}")


    # Step 9: Upload via FTP
    print("Uploading to FTP...")
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        with open(final_path, 'rb') as f:
            ftp.storbinary(f"STOR {FTP_TARGET_PATH}", f)

    print("✅ Upload complete.")

finally:
    driver.quit()
