from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains

import tempfile
import os
import time
import platform
import requests
import re
import glob
import traceback
import base64



import pandas as pd
from pathlib import Path
from ftplib import FTP
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OPENLANE_URL = "https://app.openlane.ca"
CARFAX_DIR = BASE_DIR / "static" / "carfax"
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

OPENLANE_USER = os.environ["OPENLANE_USER"]
OPENLANE_PASS = os.environ["OPENLANE_PASS"]


def save_current_page_as_pdf(driver, out_path: str):
    # Make sure the page is fully loaded before printing
    pdf = driver.execute_cdp_cmd("Page.printToPDF", {
        "printBackground": True,
        "preferCSSPageSize": True,   # respects page CSS if set
        # "paperWidth": 8.5,         # optional: Letter sizing
        # "paperHeight": 11,
        # "marginTop": 0.4, "marginBottom": 0.4, "marginLeft": 0.4, "marginRight": 0.4
    })
    data = base64.b64decode(pdf["data"])
    with open(out_path, "wb") as f:
        f.write(data)

def newest_file(pattern: str):
    files = glob.glob(pattern)
    return max(files, key=os.path.getmtime) if files else None


    while time.time() - start_ts < timeout:
        # still downloading?
        if glob.glob(os.path.join(DOWNLOAD_DIR, "*.crdownload")):
            time.sleep(0.3)
            continue

        pdfs = glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf"))
        if pdfs:
            newest = max(pdfs, key=os.path.getmtime)
            if os.path.getmtime(newest) >= start_ts - 1:
                return newest

        time.sleep(0.3)

    return None

def click_condition_report_and_switch(driver, timeout=40):
    old_handles = set(driver.window_handles)
    old_url = driver.current_url

    el = deep_find_by_exact_text(driver, "Condition report", timeout=timeout)
    js_real_click(driver, el)

    # wait until either: new tab appears OR url changes OR carfax link appears
    def progressed(d):
        if len(d.window_handles) > len(old_handles):
            return True
        if d.current_url != old_url:
            return True
        hit = d.execute_script(r"""
          const roots=[document];
          while(roots.length){
            const r=roots.shift();
            const as=r.querySelectorAll? r.querySelectorAll("a[href]"):[];
            for(const a of as){
              const h=a.href||a.getAttribute("href");
              if(h && /vhr\.carfax\.ca/i.test(h)) return true;
            }
            const els=r.querySelectorAll? r.querySelectorAll("*"):[];
            for(const el of els) if(el.shadowRoot) roots.push(el.shadowRoot);
          }
          return false;
        """)
        return bool(hit)

    WebDriverWait(driver, timeout).until(progressed)

    # switch if a new tab opened
    new_handles = [h for h in driver.window_handles if h not in old_handles]
    if new_handles:
        driver.switch_to.window(new_handles[0])

    return True

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

def normalize_vin(v) -> str:
    s = str(v or "").strip().upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
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
        signin_btn = wait.until(EC.element_to_be_clickable((By.ID, "okta-signin-submit")))
        driver.execute_script("arguments[0].click();", signin_btn)

        # ✅ wait for redirect through SAML back to Openlane
        WebDriverWait(driver, 60).until(
            lambda d: "app.openlane.ca" in d.current_url)
    
    except TimeoutException:
        driver.execute_script("""
            const btn = document.querySelector('input[type="submit"],button[type="submit"]');
            if (btn) btn.click();
        """)

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
    except TimeoutException:
        pass

def _js_click(driver, el):
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
        el
    )

def click_order_history_tab(driver, timeout=40):
    selector = '[data-testid="segment-tab-order-history"]'

    def deep_find_and_click(d):
        return d.execute_script("""
            const selector = arguments[0];

            function deepQuerySelector(selector) {
              const roots = [document];
              while (roots.length) {
                const root = roots.shift();

                // try match in this root
                const direct = root.querySelector?.(selector);
                if (direct) return direct;

                // enqueue any open shadow roots found inside this root
                const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (const el of all) {
                  if (el.shadowRoot) roots.push(el.shadowRoot);
                }
              }
              return null;
            }

            const el = deepQuerySelector(selector);
            if (!el) return false;

            el.scrollIntoView({ block: "center", inline: "center" });
            el.focus();

            const opts = { bubbles: true, cancelable: true, view: window };

            try {
              el.dispatchEvent(new PointerEvent("pointerdown", { ...opts, pointerId: 1, pointerType: "mouse", isPrimary: true }));
              el.dispatchEvent(new PointerEvent("pointerup",   { ...opts, pointerId: 1, pointerType: "mouse", isPrimary: true }));
            } catch (e) {}

            el.dispatchEvent(new MouseEvent("mousedown", opts));
            el.dispatchEvent(new MouseEvent("mouseup", opts));
            el.dispatchEvent(new MouseEvent("click", opts));

            return true;
        """, selector)

    WebDriverWait(driver, timeout).until(deep_find_and_click)


def wait_deep(driver, timeout, js_returning_element, *args):
    """Wait until execute_script returns a non-null element."""
    wait = WebDriverWait(driver, timeout)
    return wait.until(lambda d: d.execute_script(js_returning_element, *args))

def _wait_no_overlay(driver, wait, timeout=30):
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script(r"""
      // any visible overlay/backdrop/spinner?
      const sels = [
        '.pg-loading-center-middle',
        '.cdk-overlay-backdrop',
        '.mat-progress-spinner',
        'mat-spinner'
      ];
      return !sels.some(sel => {
        const el = document.querySelector(sel);
        return el && el.offsetParent !== null;
      });
    """))




def click_history_card_by_vin(driver, vin, timeout=40):
    vin = (vin or "").strip().upper()

    card = wait_deep(driver, timeout, """
        const vin = arguments[0];

        const roots = [document];
        while (roots.length) {
          const root = roots.shift();

          const cards = root.querySelectorAll?.("div.list-item") || [];
          for (const card of cards) {
            const vinEl = card.querySelector?.("div.vin");
            const txt = (vinEl?.textContent || "").trim().toUpperCase();
            if (txt === vin) return card;
          }

          const all = root.querySelectorAll?.("*") || [];
          for (const el of all) if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        return null;
    """, vin)

    js_real_click(driver, card)
    return True   



def wait_for_carfax_url(driver, timeout=15, poll=0.25):
    end = time.time() + timeout

    js = r"""
    function findCarfax() {
      const roots = [document];
      while (roots.length) {
        const r = roots.shift();

        // anchors
        const as = r.querySelectorAll ? r.querySelectorAll("a[href]") : [];
        for (const a of as) {
          const href = a.href || a.getAttribute("href");
          if (href && /vhr\.carfax\.ca/i.test(href)) return href;
        }

        // ignite-link components
        const ls = r.querySelectorAll ? r.querySelectorAll("ignite-link") : [];
        for (const l of ls) {
          const hrefAttr = l.getAttribute("href");
          if (hrefAttr && /vhr\.carfax\.ca/i.test(hrefAttr)) return hrefAttr;

          const a = l.shadowRoot && l.shadowRoot.querySelector && l.shadowRoot.querySelector("a[href]");
          const href = a && (a.href || a.getAttribute("href"));
          if (href && /vhr\.carfax\.ca/i.test(href)) return href;
        }

        // descend into shadow roots
        const els = r.querySelectorAll ? r.querySelectorAll("*") : [];
        for (const el of els) if (el.shadowRoot) roots.push(el.shadowRoot);
      }
      return null;
    }
    return findCarfax();
    """

    last = None
    while time.time() < end:
        last = driver.execute_script(js)
        if last:
            return last
        time.sleep(poll)

    return None


def deep_find_by_exact_text(driver, text: str, timeout=40):
    script = r"""
    const text = arguments[0];

    function deepQueryAll(root=document) {
      const out = [];
      const roots = [root];

      while (roots.length) {
        const r = roots.shift();
        if (r.querySelectorAll) out.push(...r.querySelectorAll("*"));

        const all = r.querySelectorAll ? r.querySelectorAll("*") : [];
        for (const el of all) if (el.shadowRoot) roots.push(el.shadowRoot);
      }
      return out;
    }

    const el = deepQueryAll()
      .find(n => n.childElementCount === 0 && n.textContent.trim() === text);

    return el || null;
    """
    end = WebDriverWait(driver, timeout)
    return end.until(lambda d: d.execute_script(script, text))

def js_real_click(driver, el):
    driver.execute_script(r"""
      const el = arguments[0];
      el.scrollIntoView({ block: "center", inline: "center" });
      el.focus();

      // if text node itself, climb to clickable
      const target = el.closest("a, button, [role='button'], .cursor-pointer") || el;

      const opts = { bubbles: true, cancelable: true, view: window };

      try {
        target.dispatchEvent(new PointerEvent("pointerdown", { ...opts, pointerId: 1, pointerType: "mouse", isPrimary: true }));
        target.dispatchEvent(new PointerEvent("pointerup",   { ...opts, pointerId: 1, pointerType: "mouse", isPrimary: true }));
      } catch (e) {}

      target.dispatchEvent(new MouseEvent("mousedown", opts));
      target.dispatchEvent(new MouseEvent("mouseup", opts));
      target.dispatchEvent(new MouseEvent("click", opts));
    """, el)


def get_carfax_url_deep(driver):
    return driver.execute_script(r"""
      function allRoots() {
        const roots = [document];
        const out = [];
        while (roots.length) {
          const r = roots.shift();
          out.push(r);
          const els = r.querySelectorAll ? r.querySelectorAll("*") : [];
          for (const el of els) if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        return out;
      }

      let best = null;

      for (const r of allRoots()) {
        // anchors
        const as = r.querySelectorAll ? r.querySelectorAll("a[href]") : [];
        for (const a of as) {
          const href = a.href || a.getAttribute("href");
          if (href && /vhr\.carfax\.ca/i.test(href)) return href;
          if (!best && href && /carfax/i.test(href)) best = href;
        }

        // ignite-link
        const ls = r.querySelectorAll ? r.querySelectorAll("ignite-link") : [];
        for (const l of ls) {
          const hrefAttr = l.getAttribute("href");
          const a = l.shadowRoot && l.shadowRoot.querySelector && l.shadowRoot.querySelector("a[href]");
          const href = hrefAttr || (a && (a.href || a.getAttribute("href")));
          if (href && /vhr\.carfax\.ca/i.test(href)) return href;
          if (!best && href && /carfax/i.test(href)) best = href;
        }
      }
      return best;
    """)


def ftp_ensure_dir(ftp, path):
    # path like "carfax" or "public_html/carfax"
    parts = [p for p in path.strip("/").split("/") if p]
    for p in parts:
        try:
            ftp.cwd(p)
        except Exception:
            ftp.mkd(p)
            ftp.cwd(p)

def ftp_upload_file(ftp, local_path, remote_name):
    with open(local_path, "rb") as f:
        ftp.storbinary(f"STOR {remote_name}", f)



def download_carfax_for_vin(driver, wait, vin: str, download_dir: str, carfax_dir: str, name_mode="last4", main_handle=None):
    vin = (vin or "").strip().upper()
    if not vin:
        return False

    def make_name(v: str):
        v = (v or "").strip().upper()
        if name_mode == "first4":
            key = v[:4]
        elif name_mode == "last4":
            key = v[-4:]
        else:  # "full"
            key = v
        return f"{key}_carfax.pdf"

    target_path = os.path.join(carfax_dir, make_name(vin))
    os.makedirs(carfax_dir, exist_ok=True)

    # If already downloaded, skip
    if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        print(f"[{vin}] START download_carfax_for_vin; current_url={driver.current_url}")

        return True

    time.sleep(1)

    print(f"[{vin}] going to purchases…")
    driver.get("https://app.openlane.ca/purchases")
    print(f"[{vin}] now at:", driver.current_url)
    print(f"[{vin}] after purchases GET; current_url={driver.current_url}")
    print(f"[{vin}] handles={driver.window_handles} current_handle={driver.current_window_handle}")

    time.sleep(1)

   # 2) Click "Order History" segment button by its label
    # Your snippet: ignite-typography ... data-label="Order History"

    print("URL before click:", driver.current_url)
    count = driver.execute_script("""
    const selector = arguments[0];

    function deepQuerySelectorAllCount(selector) {
        const roots = [document];
        let count = 0;
        while (roots.length) {
        const root = roots.shift();
        if (root.querySelectorAll) count += root.querySelectorAll(selector).length;

        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (const el of all) if (el.shadowRoot) roots.push(el.shadowRoot);
        }
        return count;
    }

    return deepQuerySelectorAllCount(selector);
    """, '[data-testid="segment-tab-order-history"]')

    print("Order history tab match", count)


    try:
        click_order_history_tab(driver)
    except Exception as e:
        print("exception clicking order history:", repr(e))
        traceback.print_exc()

    filter_order_history_by_vin(driver, vin)
     
    # Step 3: click the VIN card
    try:
        click_history_card_by_vin(driver, vin, timeout=5)
    except TimeoutException:
        print(f"[{vin}] NOT FOUND in Order History (timeout) — skipping")
        return False


    click_condition_report_and_switch(driver, timeout=5)


    carfax_url = wait_for_carfax_url(driver, timeout=5)
    print("carfax_url:", carfax_url)

    if not carfax_url:
        raise Exception("CARFAX URL never appeared (lazy-load)")

    driver.get(carfax_url)

    # 7) Save current Carfax page as a PDF (window.print() won't download in headless)
    if os.path.exists(target_path):
        os.remove(target_path)

    # give the report a moment to finish rendering
    time.sleep(2)

    save_current_page_as_pdf(driver, target_path)
    if main_handle:
        back_to_openlane(driver, main_handle)

    return os.path.exists(target_path) and os.path.getsize(target_path) > 0

if system_name == "Darwin":  # macOS
    DOWNLOAD_DIR = os.path.expanduser("~/Downloads")
elif system_name == "Linux":  # Ubuntu or other Linux
    DOWNLOAD_DIR = os.path.abspath(os.path.join(os.getcwd(), "..", "downloads"))
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

elif system_name == "Windows":
    # Use your user Downloads folder
    DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
else:
    raise Exception(f"Unsupported OS: {system_name}")

print(f"📁 Using download dir: {DOWNLOAD_DIR}")

def set_mat_select_by_text(driver, wait, data_cy: str, text: str):
    print(f"Attempting to set {data_cy} to '{text}'...")

    # 1. Click the dropdown trigger (Updated selector for new Angular Material)
    # The old '.mat-select-trigger' class is gone. We now click the <mat-select> itself.
    trigger = wait.until(EC.element_to_be_clickable((
        By.CSS_SELECTOR, f"mat-select[data-cy='{data_cy}']"
    )))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", trigger)
    driver.execute_script("arguments[0].click();", trigger)

    # 2. Wait for the options panel to open
    # We wait for <mat-option> elements to appear in the DOM
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "mat-option")))

    # 3. Find and click the specific option
    # New XPath: Finds a <mat-option> that contains the text "100" (or whatever you pass)
    # We use "contains(., text)" to find the text anywhere inside the option, 
    # ignoring the specific nested <span> structure.
    xpath = f"//mat-option[contains(., '{text}')]"
    
    try:
        option = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].click();", option)
        print(f"Selected option '{text}'")
    except TimeoutException:
        # Fallback: Print all available options to debug
        all_options = driver.find_elements(By.TAG_NAME, "mat-option")
        texts = [o.text for o in all_options]
        print(f"⚠️ Failed to find option '{text}'. Available options: {texts}")
        # Close the dropdown so the script doesn't get stuck
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        raise

    # 4. Wait for the overlay to disappear
    close_overlays(driver)

    # 5. Wait for the table to refresh (page count change usually triggers a reload)
    wait_inventory_refresh(driver, timeout=45)



def back_to_openlane(driver, main_handle):
    # close everything except main handle
    for h in list(driver.window_handles):
        if h != main_handle:
            driver.switch_to.window(h)
            driver.close()
    driver.switch_to.window(main_handle)

def set_option_selected(driver, wait, text, want=True, timeout=10):
    # Updated XPath: Looks for ANY span inside mat-list-option containing the text
    xpath = f"//mat-list-option[.//span[contains(text(), '{text}')]]"
    
    try:
        opt = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
        
        # Scroll to it just in case
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", opt)
        
        # Check if it is currently selected
        # The screenshot shows 'aria-selected="true"' when selected
        is_selected = (opt.get_attribute("aria-selected") or "").lower() == "true"
        
        if is_selected != want:
            # Click it if it's not in the state we want
            try:
                opt.click()
            except Exception:
                # Fallback to JS click if blocked
                driver.execute_script("arguments[0].click();", opt)
                
            print(f"Clicked '{text}' (Current: {is_selected} -> Want: {want})")
            time.sleep(0.5) # Short pause for animation
            
    except TimeoutException:
        print(f"⚠️ Could not find filter option '{text}'! The menu might not be open.")


def _toggle_status_option(driver, wait, text, timeout=20):
    # find the mat-list-option row that contains the label text
    opt = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((
        By.XPATH, f"//mat-list-option[.//div[contains(@class,'mat-list-text') and normalize-space()='{text}']]"
    )))
    # only click if not already selected
    selected = (opt.get_attribute("aria-selected") or "").lower() == "true" or "mat-selected" in (opt.get_attribute("class") or "")
    if not selected:
        _js_click(driver, opt)

def set_status_filters(driver, wait):
    # 1. Force the menu open
    trigger_selector = "button[data-cy='inventory-status-filter-trigger']"
    print("Attempting to open Status Filter menu...")
    
    btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, trigger_selector)))
    driver.execute_script("arguments[0].click();", btn)

    # 2. Wait for the options to actually appear in the DOM
    # We look for the exact element from your screenshot
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "mat-list-option")))

    # 3. Now select the items using the new function
    set_option_selected(driver, wait, "In Stock", True)
    set_option_selected(driver, wait, "Coming Soon", True)
    set_option_selected(driver, wait, "In Trade", False)
    set_option_selected(driver, wait, "Deal Pending", False)

    # 4. Close the menu
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)

def close_overlays(driver, timeout=5):
    # Best-effort: try to close overlays but don't die if it lingers
    end = time.time() + timeout
    while time.time() < end:
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        try:
            driver.execute_script("""
              const b = document.querySelector('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing');
              if (b) b.click();
            """)
        except Exception:
            pass

        gone = driver.execute_script("""
          const b = document.querySelector('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing');
          return !b;
        """)
        if gone:
            return True

        time.sleep(0.2)

    # don't raise TimeoutException
    return False

def wait_no_backdrop(driver, timeout=30):
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script("""
      const b = document.querySelector('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing');
      if (!b) return true;
      // if it's in DOM but not visible, treat as gone
      const style = window.getComputedStyle(b);
      return (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0');
    """))
def wait_inventory_refresh(driver, timeout=45):
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script(r"""
      // 1) no visible overlay/backdrop/spinner
      const blockers = [
        '.pg-loading-center-middle',
        '.cdk-overlay-backdrop.cdk-overlay-backdrop-showing',
        '.mat-progress-spinner',
        'mat-spinner'
      ];
      for (const sel of blockers) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) return false;
      }

      // 2) inventory table exists and has rendered rows (tbody tr)
      const table = document.querySelector('table#inventory-table');
      if (!table) return false;

      const rows = table.querySelectorAll('tbody tr');
      return rows.length > 0;
    """))

def filter_order_history_by_vin(driver, vin: str,timeout=20):
    vin = (vin or "").strip().upper()

    el = WebDriverWait(driver, timeout).until(lambda d: d.execute_script(r"""
        function deepQuerySelector(selector) {
        const roots = [document];
        while (roots.length) {
            const r = roots.shift();
            const el = r.querySelector?.(selector);
            if (el) return el;
            const all = r.querySelectorAll ? r.querySelectorAll("*") : [];
            for (const node of all) if (node.shadowRoot) roots.push(node.shadowRoot);
        }
        return null;
        }
        return deepQuerySelector('input[data-test="vin-search"]');
    """))

    driver.execute_script(r"""
        const el = arguments[0];
        const val = arguments[1];
        el.focus();
        el.value = val;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
    """, el, vin)

    time.sleep(1)

# --- Setup Chrome ---
chrome_options = Options()

prefs = {
    "download.default_directory": DOWNLOAD_DIR,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True,
    "plugins.always_open_pdf_externally": True,
}

chrome_options.add_experimental_option("prefs", prefs)
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument("--start-maximized") # Force max window
chrome_options.add_argument("--window-size=1920,1080")

chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

if platform.system() != "Windows":
    chrome_options.add_argument("--headless=new")

chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
driver = webdriver.Chrome(options=chrome_options)

try:

    ''' ***************************start of --codded out for debugging ---***************************  '''
    

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
    wait = WebDriverWait(driver, 20)

    
    WebDriverWait(driver, 30).until(lambda d: d.execute_script("""
    const b = document.querySelector('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing');
    return !b;
    """))
       
    set_status_filters(driver, wait)
    time.sleep(1)
    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    wait_no_backdrop(driver, 30)   # from earlier

    wait.until(EC.presence_of_element_located((By.ID, "select-all")))

    


    
    print("Opening field selector dropdown...")
    dropdown_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button#colDropdown"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", dropdown_button)

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

    time.sleep(1)

    driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    time.sleep(0.3)

    
    print("Changing page size to 100...")


    set_mat_select_by_text(driver, wait,"page-count", "100")

    # Wait for the page to refresh with 100 cars
    time.sleep(1)

   

    # Step 5: Click 'Select All' checkbox    
    print("Clicking select-all checkbox...")
    # wait until the table + select-all is actually clickable
    select_all = WebDriverWait(driver, 30).until(
    EC.presence_of_element_located((By.CSS_SELECTOR, "input#select-all"))
)
    driver.execute_script("""
    const cb = arguments[0];
    cb.scrollIntoView({block:'center'});
    if (!cb.checked) cb.click();
    cb.dispatchEvent(new Event('change', {bubbles:true}));
    """, select_all)

    close_overlays(driver)
    wait_inventory_refresh(driver, timeout=45)  

    # Step 6: Click Export button
    export_button = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Export')]"))
    )
    export_button.click()
    print("Exporting inventory...")

    # Step 7: Wait for download
    time.sleep(2)


    
   
    '''   *************************** end of coded out for debugging *************************** '''


    # Step 8: Rename and upload
    downloaded_path = os.path.join(DOWNLOAD_DIR, EXPORTED_FILENAME)
    final_path = os.path.join(DOWNLOAD_DIR, FINAL_FILENAME)

    if os.path.exists(downloaded_path):
        if os.path.exists(final_path):
            print ("final path exists, deleting final path: ", final_path)
            os.remove(final_path)
        os.rename(downloaded_path, final_path)
        print(f"Renamed to: {final_path}")
    elif os.path.exists(final_path):
        print(f"Already exists as: {final_path}")    
    else:
        raise FileNotFoundError(f"{EXPORTED_FILENAME} not found in {DOWNLOAD_DIR}")

    

    # ✅ NEW: read VINs + download missing carfax PDFs
    df = pd.read_csv(final_path, engine="python", on_bad_lines="skip")

    if "VIN" not in df.columns and "vin" in df.columns:
        df = df.rename(columns={"vin": "VIN"})

    # ✅ ensure Links exists (React/UI reads this)
    if "Links" not in df.columns:
        df["Links"] = ""
    df["Links"] = df["Links"].fillna("").astype(str)

    vins = [normalize_vin(v) for v in df["VIN"].dropna().tolist()]
    vins = [v for v in vins if len(v) == 17]  # keep only valid VINs

    print("VIN count:", len(vins))
    print("First 25 VINs:")
    for i, v in enumerate(vins[:25], 1):
        print(i, repr(v), "len=", len(v))

    bad = [v for v in vins if len(v) != 17]
    print("Bad VINs (len != 17):", len(bad))
    for v in bad[:25]:
        print("BAD", repr(v), "len=", len(v))

    login_openlane(driver, wait)   # login ONCE
    OPENLANE_HANDLE = driver.current_window_handle

    failed_vins = []
    existing = list(Path(CARFAX_DIR).glob("*_carfax.pdf"))
    print("Existing PDFs in CARFAX_DIR:", len(existing))

    for vin in vins:
        print("\nProcessing VIN:", repr(vin), "len=", len(vin), "last8=", vin[-8:])

        try:
            target_pdf = Path(CARFAX_DIR) / f"{vin}_carfax.pdf"


            if target_pdf.exists() and target_pdf.stat().st_size > 0:
                df.loc[df["VIN"].astype(str).str.strip().str.upper() == vin, "Links"] = \
                    f"https://berlinautosales.ca/carfax/{vin}_carfax.pdf"
                print("pdf exists locally, skipping download")
                continue

            print("carfax for this vin ", vin, " doesn't exist,")
            print("Before download, URL is:", driver.current_url)

            #name mode can be last4, first4, or full
            ok = download_carfax_for_vin(
                driver, wait, vin, DOWNLOAD_DIR, str(CARFAX_DIR),
                name_mode="full", main_handle=OPENLANE_HANDLE
            )

            if ok:
                df.loc[df["VIN"].astype(str).str.strip().str.upper() == vin, "Links"] = f"https://berlinautosales.ca/carfax/{vin}_carfax.pdf"
            else:
                df.loc[df["VIN"].astype(str).str.strip().str.upper() == vin, "Links"] = ""

            print(vin, "carfax:", "OK" if ok else "FAILED")
            if not ok:
                failed_vins.append(vin)

        except Exception as e:
            print(f"[{vin}] ERROR (skipping): {repr(e)}")
            df.loc[df["VIN"].astype(str).str.strip().str.upper() == vin, "Links"] = ""
            traceback.print_exc()
            failed_vins.append(vin)

            # try to recover browser state so the next VIN can proceed
            try:
                back_to_openlane(driver, OPENLANE_HANDLE)
            except Exception:
                pass

            continue

    # optional: dump failures so you can inspect later
    if failed_vins:
        fail_path = BASE_DIR / "failed_vins.txt"
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write("\n".join(failed_vins))
        print(f"\n⚠️ Failed VINs written to: {fail_path}")




        # Step 9: Upload via FTP


    def ftp_cwd_p(ftp, path: str):
        # Make dirs and cd into them, relative to current dir
        for part in [p for p in path.strip("/").split("/") if p]:
            try:
                ftp.cwd(part)
            except Exception:
                ftp.mkd(part)
                ftp.cwd(part)
                
    # write updated CSV (with Links) BEFORE uploading
    df.to_csv(final_path, index=False)
    backend_inventory = str(BASE_DIR / "inventory.csv")
    import shutil
    shutil.copy2(final_path, backend_inventory)
    print("Copied inventory.csv to:", backend_inventory)

    print("Uploading to FTP...")
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        print("PWD at login:", ftp.pwd())
        print("Root listing:", ftp.nlst())

        # 1) Upload inventory.csv to web root (adjust if your host needs public_html/)
        base_dir = ftp.pwd()  # this is the directory inventory.csv will be uploaded into
        print("FTP base dir:", base_dir)

        # 1) Upload inventory.csv into base_dir
        with open(final_path, "rb") as f:
            ftp.storbinary("STOR inventory.csv", f)

        print("After inventory upload, PWD:", ftp.pwd())


        def ensure_dir(ftp, dirname):
            try:
                ftp.cwd(dirname)
            except Exception:
                ftp.mkd(dirname)
                ftp.cwd(dirname)

        ensure_dir(ftp, "carfax")

        print("Uploading PDFs into:", ftp.pwd())  # should be .../public_html/carfax
        local_pdfs = sorted(Path(CARFAX_DIR).glob("*_carfax.pdf"))
        print(f"Uploading {len(local_pdfs)} PDFs to {ftp.pwd()} ...")

        for p in local_pdfs:
            if p.stat().st_size <= 0:
                continue
            with open(p, "rb") as f:
                ftp.storbinary(f"STOR {p.name}", f)

    print("✅ Upload complete.")

finally:
    driver.quit()
