# --- update_inventory.py ---
import os
import time
import platform
import tempfile
import logging
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from ftplib import FTP

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')

# --- Constants ---
system_name = platform.system()
DOWNLOAD_DIR = os.path.abspath(os.path.join(os.getcwd(), "..", "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
EXPORTED_FILENAME = "inventory_export.csv"
FINAL_FILENAME = "inventory.csv"
CARFAX_FOLDER = "carfax"
os.makedirs(CARFAX_FOLDER, exist_ok=True)

FTP_HOST = "ftp.eddysauto.ca"
FTP_USER = "berlinautosales.ca@berlinautosales.ca"
FTP_PASS = "B2010luetooth5!"
FTP_TARGET_PATH = "inventory.csv"
FTP_CARFAX_DIR = "carfax"

LOGIN_EMAIL = "farhad@berlinautosales.ca"
LOGIN_PASS = "B2010luetooth5!"

# --- Setup Chrome ---
chrome_options = Options()
prefs = {"download.default_directory": DOWNLOAD_DIR, "download.prompt_for_download": False, "safebrowsing.enabled": True}
chrome_options.add_experimental_option("prefs", prefs)
chrome_options.add_argument('--headless=new')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--window-size=1920,1080')
chrome_options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")

driver = webdriver.Chrome(options=chrome_options)

try:
    driver.get("https://app.dealerpull.com/login")
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(LOGIN_EMAIL)
    driver.find_element(By.NAME, "password").send_keys(LOGIN_PASS + Keys.RETURN)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[ui-view='root']")))
    time.sleep(3)

    driver.get("https://app.dealerpull.com/inventory-list")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "select-all")))
    time.sleep(1)

    dropdown_button = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#colDropdown")))
    dropdown_button.click()
    time.sleep(1)

    fields_to_check = ["vin", "description", "links", "images", "List price"]
    checkboxes = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show input[type='checkbox']")
    labels = driver.find_elements(By.CSS_SELECTOR, "ul.dropdown-menu.show .custom-control-label")

    for i, label in enumerate(labels):
        if label.text.strip().lower() in [f.lower() for f in fields_to_check]:
            box = checkboxes[i]
            if box.get_attribute("ng-reflect-model") != "true":
                driver.execute_script("arguments[0].click();", box)

    WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select[data-cy='page-count']"))).click()
    driver.execute_script("document.querySelector('select[data-cy=\"page-count\"]').value = '100';")
    driver.execute_script("document.querySelector('select[data-cy=\"page-count\"]').dispatchEvent(new Event('change'));")
    time.sleep(3)

    driver.find_element(By.ID, "select-all").click()
    export_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
    )
    export_button.click()
    time.sleep(10)

    downloaded_path = os.path.join(DOWNLOAD_DIR, EXPORTED_FILENAME)
    final_path = os.path.join("static", FINAL_FILENAME)
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(downloaded_path, final_path)

    df = pd.read_csv(final_path, on_bad_lines="skip")
    df.columns = [c.strip().lower() for c in df.columns]

    for _, row in df.iterrows():
        vin = str(row.get('vin', '')).strip()
        link = str(row.get('links', '')).strip()
        if vin and link and link.startswith('http'):
            last6 = vin[-6:]
            path = os.path.join(CARFAX_FOLDER, f"{last6}_carfax.pdf")
            try:
                res = requests.get(link, timeout=10)
                if res.status_code == 200:
                    with open(path, 'wb') as f:
                        f.write(res.content)
            except Exception:
                continue

    # Upload everything
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        with open(final_path, 'rb') as f:
            ftp.storbinary(f"STOR {FTP_TARGET_PATH}", f)

        try:
            ftp.mkd(FTP_CARFAX_DIR)
        except Exception:
            pass
        ftp.cwd(FTP_CARFAX_DIR)
        for file in os.listdir(CARFAX_FOLDER):
            with open(os.path.join(CARFAX_FOLDER, file), 'rb') as f:
                ftp.storbinary(f"STOR {file}", f)

    # Generate available_carfax.csv
    carfax_files = set(os.listdir(CARFAX_FOLDER))
    available_carfax = []
    for _, row in df.iterrows():
        vin = str(row.get('vin', '')).strip()
        desc = str(row.get('description', '')).strip()
        last6 = vin[-6:]
        if f"{last6}_carfax.pdf" in carfax_files:
            available_carfax.append({
                'vin': vin,
                'description': desc,
                'carfax_url': f"https://berlinautosales.ca/carfax/{last6}_carfax.pdf"
            })
    pd.DataFrame(available_carfax).to_csv(os.path.join("static", "available_carfax.csv"), index=False)

finally:
    driver.quit()
    logging.info("🛑 Selenium session closed.")
