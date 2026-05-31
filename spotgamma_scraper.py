import time
import re
import os
import json
from datetime import datetime

try:
    import keyring
    _KEYRING_OK = True
except ImportError:
    _KEYRING_OK = False

ENGINE_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.py")
_CACHE_DIR   = os.path.dirname(os.path.abspath(__file__))
_KEYRING_SVC = "gibbz_spotgamma"

SG_URL = "https://dashboard.spotgamma.com/home?eh-model=legacy"

CHROME_PROFILE = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Google", "Chrome", "User Data"
)


def save_credentials(email: str, password: str) -> None:
    if not _KEYRING_OK:
        print("[SG] keyring unavailable — credentials not saved")
        return
    try:
        keyring.set_password(_KEYRING_SVC, "email",    email)
        keyring.set_password(_KEYRING_SVC, "password", password)
    except Exception as e:
        print(f"[SG] save_credentials failed: {e}")


def load_credentials():
    if not _KEYRING_OK:
        return None, None
    try:
        email    = keyring.get_password(_KEYRING_SVC, "email")
        password = keyring.get_password(_KEYRING_SVC, "password")
        return email, password
    except Exception as e:
        print(f"[SG] load_credentials failed: {e}")
        return None, None


def _cache_path(today: str) -> str:
    return os.path.join(_CACHE_DIR, f"spotgamma_cache_{today}.json")


def save_levels_cache(levels: dict) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(_cache_path(today), "w", encoding="utf-8") as f:
            json.dump(levels, f)
    except Exception as e:
        print(f"[SG] cache write failed: {e}")


def load_levels_cache() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    path  = _cache_path(today)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  [SG] WARNING: usando cache del día {today} — scrape fallido")
        return data
    except Exception as e:
        print(f"[SG] cache read failed: {e}")
        return {}


def fetch_levels_with_profile() -> dict:
    """
    Uses existing Chrome profile where SpotGamma session is active.
    No login needed — uses saved cookies from real Chrome.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError as e:
        print("  ERROR: " + str(e))
        return {}

    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # Use existing Chrome profile with saved session
    if os.path.exists(CHROME_PROFILE):
        opts.add_argument("--user-data-dir=" + CHROME_PROFILE)
        opts.add_argument("--profile-directory=Default")
        print("  Usando perfil Chrome existente...")
    else:
        print("  ADVERTENCIA: Perfil Chrome no encontrado en:")
        print("  " + CHROME_PROFILE)

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=opts)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Go directly to dashboard — session should be active
        print("  Cargando dashboard SpotGamma...")
        driver.get(SG_URL)
        time.sleep(8)

        driver.save_screenshot("sg_debug_dashboard.png")

        current_url = driver.current_url
        print("  URL: " + current_url)

        if "login" in current_url.lower():
            print("  Sesion no activa en Chrome — intentando login manual...")
            return {}

        print("  Sesion activa. Extrayendo niveles...")

        # Remove popup with JavaScript
        try:
            driver.execute_script("""
                var modals = document.querySelectorAll(
                    '[class*="modal"],[class*="Modal"],[class*="popup"],
                     [class*="Popup"],[class*="overlay"],[class*="Overlay"]'
                );
                modals.forEach(function(m) { m.style.display = 'none'; });
            """)
            time.sleep(1)
        except Exception:
            pass

        driver.save_screenshot("sg_debug_no_popup.png")

        # Extract levels via JavaScript
        levels = {}
        try:
            table_data = driver.execute_script("""
                var results = [];
                var rows = document.querySelectorAll('tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        var es  = cells[1] ? cells[1].innerText.trim() : '';
                        var lid = cells[2] ? cells[2].innerText.trim() : '';
                        if (lid && es) {
                            results.push([es, lid]);
                        }
                    }
                });
                return results;
            """)

            if table_data:
                print("  Filas encontradas: " + str(len(table_data)))
                for row in table_data:
                    try:
                        es  = str(row[0]).replace(",", "").strip()
                        lid = str(row[1]).strip()
                        if lid and es:
                            levels[lid] = float(es)
                    except Exception:
                        pass
        except Exception as e:
            print("  Error JS: " + str(e))

        # Fallback: HTML parsing
        if not levels:
            print("  Intentando parsing HTML...")
            page_source = driver.page_source
            with open("sg_debug_source.html", "w", encoding="utf-8") as f:
                f.write(page_source)
            levels = parse_levels_from_html(page_source)

        return levels

    except Exception as e:
        print("  ERROR: " + str(e))
        if driver:
            try:
                driver.save_screenshot("sg_debug_error.png")
            except Exception:
                pass
        return {}

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def fetch_levels_with_login(email: str, password: str) -> dict:
    """
    Falls back to full login if Chrome profile doesn't work.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError as e:
        print("  ERROR: " + str(e))
        return {}

    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=opts)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        wait = WebDriverWait(driver, 20)

        print("  Abriendo pagina de login...")
        driver.get("https://dashboard.spotgamma.com/login")
        time.sleep(4)

        inputs = driver.find_elements(By.TAG_NAME, "input")
        email_field    = None
        password_field = None

        for inp in inputs:
            inp_type = inp.get_attribute("type") or ""
            if inp_type == "password":
                password_field = inp
            elif inp_type in ("email", "text") or not inp_type:
                if email_field is None:
                    email_field = inp

        if email_field is None and len(inputs) >= 1:
            email_field = inputs[0]
        if password_field is None and len(inputs) >= 2:
            password_field = inputs[1]

        if not email_field or not password_field:
            print("  ERROR: campos no encontrados")
            return {}

        # Fill fields using ActionChains (more human-like)
        actions = ActionChains(driver)
        actions.click(email_field).send_keys(email).perform()
        time.sleep(0.8)
        actions.click(password_field).send_keys(password).perform()
        time.sleep(0.8)

        driver.save_screenshot("sg_debug_filled.png")

        # Click Login button — multiple strategies
        clicked = False
        try:
            btn = driver.find_element(
                By.XPATH,
                "//button[contains(text(),'Login') or contains(text(),'login')]"
            )
            ActionChains(driver).move_to_element(btn).click().perform()
            clicked = True
        except Exception:
            pass

        if not clicked:
            try:
                password_field.send_keys(Keys.RETURN)
                clicked = True
            except Exception:
                pass

        print("  Esperando autenticacion...")
        time.sleep(8)

        driver.save_screenshot("sg_debug_after_login.png")

        if "login" in driver.current_url.lower():
            print("  Login fallo.")
            return {}

        print("  Login exitoso.")

        # Navigate to dashboard
        print("  Cargando dashboard...")
        driver.get(SG_URL)
        time.sleep(8)

        driver.save_screenshot("sg_debug_dashboard.png")

        # Remove popup
        try:
            driver.execute_script("""
                var modals = document.querySelectorAll(
                    '[class*="modal"],[class*="Modal"],[class*="popup"],
                     [class*="Popup"],[class*="overlay"],[class*="Overlay"]'
                );
                modals.forEach(function(m) { m.style.display = 'none'; });
            """)
            time.sleep(1)
        except Exception:
            pass

        # Extract levels
        levels = {}
        try:
            table_data = driver.execute_script("""
                var results = [];
                var rows = document.querySelectorAll('tr');
                rows.forEach(function(row) {
                    var cells = row.querySelectorAll('td');
                    if (cells.length >= 3) {
                        var es  = cells[1] ? cells[1].innerText.trim() : '';
                        var lid = cells[2] ? cells[2].innerText.trim() : '';
                        if (lid && es) {
                            results.push([es, lid]);
                        }
                    }
                });
                return results;
            """)
            if table_data:
                for row in table_data:
                    try:
                        es  = str(row[0]).replace(",", "").strip()
                        lid = str(row[1]).strip()
                        if lid and es:
                            levels[lid] = float(es)
                    except Exception:
                        pass
        except Exception as e:
            print("  Error JS: " + str(e))

        if not levels:
            page_source = driver.page_source
            with open("sg_debug_source.html", "w", encoding="utf-8") as f:
                f.write(page_source)
            levels = parse_levels_from_html(page_source)

        return levels

    except Exception as e:
        print("  ERROR: " + str(e))
        if driver:
            try:
                driver.save_screenshot("sg_debug_error.png")
            except Exception:
                pass
        return {}

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def parse_levels_from_html(html: str) -> dict:
    levels = {}
    level_names = [
        "Call Wall", "Put Wall", "Zero Gamma",
        "Volatility Trigger",
        "Large Gamma 1", "Large Gamma 2",
        "Large Gamma 3", "Large Gamma 4",
        "Combo 1", "Combo 2", "Combo 3", "Combo 4",
    ]
    for name in level_names:
        patterns = [
            r'(\d{4,5}(?:\.\d+)?)[^<]{0,50}' + re.escape(name),
            re.escape(name) + r'[^<]{0,50}(\d{4,5}(?:\.\d+)?)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                try:
                    levels[name] = float(matches[-1])
                    break
                except ValueError:
                    pass
    return levels


def update_engine(vah: float, poc: float, val: float) -> bool:
    try:
        with open(ENGINE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"^VAH\s*=\s*[\d.]+", "VAH = " + str(vah), content, flags=re.MULTILINE)
        content = re.sub(r"^POC\s*=\s*[\d.]+", "POC = " + str(poc), content, flags=re.MULTILINE)
        content = re.sub(r"^VAL\s*=\s*[\d.]+", "VAL = " + str(val), content, flags=re.MULTILINE)
        with open(ENGINE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print("  ERROR: " + str(e))
        return False


def run():
    print("\033[2J\033[H", end="", flush=True)
    today = datetime.now().strftime("%A %d/%m/%Y  %H:%M CR")
    print("=" * 55)
    print("  GIBBZ — SpotGamma Alpha Auto-Scraper")
    print("  " + today)
    print("=" * 55)
    print()

    saved_email, saved_password = load_credentials()

    if saved_email and saved_password:
        print("  Credenciales guardadas:")
        print("  Email: " + saved_email)
        cambiar = input("  Usar estas? (Enter=si / n=cambiar): ").strip().lower()
        if cambiar == "n":
            email    = input("  Email    : ").strip()
            password = input("  Password : ").strip()
            save_credentials(email, password)
        else:
            email    = saved_email
            password = saved_password
    else:
        print("  Primera vez — ingresa tus credenciales:")
        print()
        email    = input("  Email    : ").strip()
        password = input("  Password : ").strip()
        save_credentials(email, password)
        print("  Credenciales guardadas.")

    print()
    print("  Iniciando scraper (40-60 segundos)...")
    print()

    # Strategy 1: Use Chrome profile with saved session
    levels = fetch_levels_with_profile()

    # Strategy 2: Full login fallback
    if not levels:
        print()
        print("  Perfil Chrome no funciono. Intentando login directo...")
        print()
        levels = fetch_levels_with_login(email, password)

    if levels:
        save_levels_cache(levels)
    else:
        levels = load_levels_cache()

    if not levels:
        print()
        print("  No se pudieron obtener niveles automaticamente.")
        print("  Usa levels_input.py para ingreso manual.")
        input("\n  Presiona Enter para cerrar...")
        return

    print()
    print("  NIVELES ENCONTRADOS EN /ES:")
    print()
    for lid, price in sorted(levels.items(), key=lambda x: x[1], reverse=True):
        print("  " + str(price) + "  ->  " + lid)
    print()

    call_wall  = levels.get("Call Wall",          0)
    zero_gamma = levels.get("Zero Gamma",         0)
    put_wall   = levels.get("Put Wall",           0)
    vol_trig   = levels.get("Volatility Trigger", 0)

    vah = call_wall
    poc = zero_gamma if zero_gamma > 0 else vol_trig
    val = put_wall

    print("=" * 55)
    print("  NIVELES PARA GIBBZ:")
    print("=" * 55)
    print()
    print("  VAH  =  " + str(vah) + "  (Call Wall)")
    print("  POC  =  " + str(poc) + "  (Zero Gamma)")
    print("  VAL  =  " + str(val) + "  (Put Wall)")
    print()

    if vah <= 0 or poc <= 0 or val <= 0:
        print("  ERROR: Niveles incompletos.")
        print("  Usa levels_input.py para ingreso manual.")
        input("\n  Presiona Enter para cerrar...")
        return

    if not (val < poc < vah):
        print("  ADVERTENCIA: Orden VAL < POC < VAH incorrecto.")
        confirm = input("  Continuar? (s/n): ").strip().lower()
        if confirm != "s":
            return

    print("  Actualizando engine.py...")
    ok = update_engine(vah, poc, val)

    if ok:
        print()
        print("=" * 55)
        print("  SISTEMA ACTUALIZADO CON DATOS SPOTGAMMA")
        print("  Ejecuta: python engine.py")
        print("=" * 55)
    else:
        print("  FALLO — usa levels_input.py manualmente.")

    print()
    input("  Presiona Enter para cerrar...")


if __name__ == "__main__":
    run()