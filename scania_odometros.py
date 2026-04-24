"""
scania_odometros.py
Usa la API interna de Scania Fleet Position para obtener odómetros.
Endpoint: GET https://fleetposition.do.prod.gf.aws.scania.com/v1/equipment/lastKnownAddress
Token: OAuth Bearer del portal my.scania.com (renovado via Selenium)
"""

import os
import re
import json
import time
import tempfile
import requests
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

import gspread
from google.oauth2.service_account import Credentials

# ─── CREDENCIALES DESDE GITHUB SECRETS ──────────────────────────────────────
SCANIA_USUARIO  = os.environ["SCANIA_USUARIO"]
SCANIA_PASSWORD = os.environ["SCANIA_PASSWORD"]
SHEET_ID        = os.environ["SHEET_ID"]
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS_JSON"]

# ─── URLS ────────────────────────────────────────────────────────────────────
LOGIN_URL    = "https://my.scania.com/start"
API_BASE_URL = "https://fleetposition.do.prod.gf.aws.scania.com"
API_ENDPOINT = f"{API_BASE_URL}/v1/equipment/lastKnownAddress"

# ─── CONFIGURACIÓN SHEETS ────────────────────────────────────────────────────
PESTANAS = [
    "Services-LAD",
    "Services-BUE",
    "Services-CAT",
    "Services-COR",
    "Services-LRJ",
    "Services-TUC",
]

COL_PATENTE   = 0
COL_KM_ACTUAL = 7


# ─── HELPERS ────────────────────────────────────────────────────────────────
def normalizar_patente(texto: str) -> str:
    return re.sub(r"\s+", "", str(texto)).upper().strip()


# ─── PASO 1: OBTENER TOKEN VIA SELENIUM ─────────────────────────────────────
def obtener_token_selenium() -> str:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando Chrome...")

    opciones = Options()
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--window-size=1280,900")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    opciones.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=opciones)
    wait   = WebDriverWait(driver, 30)
    token  = None

    try:
        # ── Login via my.scania.com ────────────────────────────────────────
        print(f"Navegando a my.scania.com...")
        driver.get("https://my.scania.com/start")
        time.sleep(6)
        driver.save_screenshot("s1_inicio.png")
        print(f"URL: {driver.current_url} | Título: {driver.title}")

        # Aceptar cookies en mylogin.scania.com
        for sel in ["//button[contains(text(),'I accept')]", "//button[contains(text(),'Acepto')]"]:
            try:
                btn = driver.find_element(By.XPATH, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Cookies aceptadas")
                time.sleep(3)
                break
            except:
                pass

        driver.save_screenshot("s2_post_cookies.png")
        print(f"URL post-cookies: {driver.current_url}")

        # Buscar el iframe de Keycloak que contiene el formulario
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        print(f"Iframes: {len(iframes)}")
        for i, iframe in enumerate(iframes):
            src = iframe.get_attribute("src") or ""
            print(f"  iframe[{i}] src={src[:80]}")

        # El formulario real está en la página principal (no iframe)
        # pero el campo puede ser un web component — buscar en shadow DOM
        campo_email = None

        # Intentar encontrar el campo dentro del shadow DOM
        campo_email = driver.execute_script("""
            // Buscar recursivamente en shadow DOMs
            function findInShadow(root, selector) {
                var el = root.querySelector(selector);
                if (el) return el;
                var children = root.querySelectorAll('*');
                for (var i = 0; i < children.length; i++) {
                    if (children[i].shadowRoot) {
                        var found = findInShadow(children[i].shadowRoot, selector);
                        if (found) return found;
                    }
                }
                return null;
            }
            return findInShadow(document, "input[name='email'], input[type='email'], #username, #email");
        """)

        if campo_email:
            print(f"Campo email encontrado en shadow DOM")
            # Para shadow DOM, ejecutar todo dentro del contexto correcto
            val = driver.execute_script("""
                function findAndFill(root, selector, value) {
                    var el = root.querySelector(selector);
                    if (el) {
                        el.focus();
                        // Simular escritura tecla por tecla
                        for (var i = 0; i < value.length; i++) {
                            var char = value[i];
                            var inputEvent = new InputEvent('input', {
                                bubbles: true,
                                cancelable: true,
                                data: char,
                                inputType: 'insertText'
                            });
                            // Modificar el valor directamente en el contexto del elemento
                            var descriptor = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value') ||
                                             Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                            if (descriptor && descriptor.set) {
                                descriptor.set.call(el, el.value + char);
                            }
                            el.dispatchEvent(inputEvent);
                        }
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return el.value;
                    }
                    // Buscar en shadow roots
                    var children = root.querySelectorAll('*');
                    for (var i = 0; i < children.length; i++) {
                        if (children[i].shadowRoot) {
                            var result = findAndFill(children[i].shadowRoot, selector, value);
                            if (result !== null) return result;
                        }
                    }
                    return null;
                }
                return findAndFill(document, "input[name='email'], input[type='email'], input[type='text']", arguments[0]);
            """, SCANIA_USUARIO)
            print(f"Email ingresado: len={len(val) if val else 0}")
        else:
            print("Campo email NO encontrado en shadow DOM")
            # Listar todos los elementos interactivos
            todos = driver.execute_script("""
                var result = [];
                document.querySelectorAll('input, button').forEach(function(el) {
                    result.push(el.tagName + ' type=' + el.type + ' id=' + el.id + ' name=' + el.name);
                });
                return result;
            """)
            print(f"Elementos interactivos: {todos}")

        driver.save_screenshot("s3_campo_email.png")

        # Botón Continue
        for sel in ["//button[contains(text(),'Continue')]", "//button[contains(text(),'Continuar')]", "button[type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Continue clickeado: {sel}")
                break
            except:
                continue

        time.sleep(8)
        driver.save_screenshot("s4_post_continue.png")
        print(f"URL post-Continue: {driver.current_url}")

        # Campo contraseña
        campo_pass = None
        campo_pass = driver.execute_script("""
            function findInShadow(root, selector) {
                var el = root.querySelector(selector);
                if (el) return el;
                var children = root.querySelectorAll('*');
                for (var i = 0; i < children.length; i++) {
                    if (children[i].shadowRoot) {
                        var found = findInShadow(children[i].shadowRoot, selector);
                        if (found) return found;
                    }
                }
                return null;
            }
            return findInShadow(document, "input[type='password']");
        """)

        # Usar la misma función de shadow DOM para la contraseña
        val_pass = driver.execute_script("""
            function findAndFill(root, selector, value) {
                var el = root.querySelector(selector);
                if (el) {
                    el.focus();
                    for (var i = 0; i < value.length; i++) {
                        var char = value[i];
                        var descriptor = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value') ||
                                         Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                        if (descriptor && descriptor.set) {
                            descriptor.set.call(el, el.value + char);
                        }
                        el.dispatchEvent(new InputEvent('input', {bubbles: true, data: char, inputType: 'insertText'}));
                    }
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return el.value.length;
                }
                var children = root.querySelectorAll('*');
                for (var i = 0; i < children.length; i++) {
                    if (children[i].shadowRoot) {
                        var result = findAndFill(children[i].shadowRoot, selector, value);
                        if (result !== null) return result;
                    }
                }
                return null;
            }
            return findAndFill(document, "input[type='password']", arguments[0]);
        """, SCANIA_PASSWORD)
        print(f"Password ingresado: len={val_pass}")

        time.sleep(0.5)

        # Botón login
        for sel in ["//button[contains(text(),'Log in')]", "//button[contains(text(),'Sign in')]", "button[type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Login clickeado")
                break
            except:
                continue

        print(f"Esperando redirección...")
        time.sleep(15)
        driver.save_screenshot("s5_post_login.png")
        print(f"URL final: {driver.current_url}")

        # ── Navegar al portal y capturar token ────────────────────────────
        driver.get("https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list")
        time.sleep(10)

        for _ in range(2):
            logs = driver.get_log("performance")
            for entry in logs:
                try:
                    msg = json.loads(entry["message"])["message"]
                    if msg.get("method") == "Network.requestWillBeSent":
                        req = msg.get("params", {}).get("request", {})
                        auth = req.get("headers", {}).get("Authorization", "")
                        url = req.get("url", "")
                        if auth.startswith("Bearer ") and "fleetposition" in url:
                            token = auth.replace("Bearer ", "")
                            print(f"✅ Token interceptado ({len(token)} chars)")
                            break
                except:
                    continue
            if token:
                break
            time.sleep(5)

    except Exception as e:
        print(f"❌ Error: {e}")
        try:
            driver.save_screenshot("error_final.png")
        except:
            pass
        raise
    finally:
        driver.quit()

    if not token:
        raise Exception("No se pudo obtener el token")

    return token


# ─── PASO 2: OBTENER ODÓMETROS VIA API ──────────────────────────────────────
def extraer_odometros_scania() -> dict:
    token = obtener_token_selenium()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "x-client": "cs_fmp_fleetposition_app",
        "Origin": "https://fmp-fleetposition.cs.scania.com",
        "Referer": "https://fmp-fleetposition.cs.scania.com/",
    }

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Llamando API lastKnownAddress...")
    resp = requests.get(API_ENDPOINT, headers=headers, timeout=30)
    print(f"  Status: {resp.status_code}")

    odometros = {}

    if resp.status_code == 200:
        data = resp.json()
        print(f"  Vehículos en respuesta: {len(data)}")

        for vehiculo in data:
            patente_raw = vehiculo.get("equipment", {}).get("registrationNumber", "")
            km_metros   = vehiculo.get("odometerInMeters", 0)

            if patente_raw and km_metros:
                patente = normalizar_patente(patente_raw)
                km      = int(km_metros) // 1000
                odometros[patente] = km
                print(f"  ✅ {patente}: {km:,} km")
    else:
        print(f"  Error: {resp.text[:300]}")

    print(f"\nTotal Scania: {len(odometros)} vehículos")
    return odometros


# ─── PASO 3: ACTUALIZAR SHEETS ───────────────────────────────────────────────
def actualizar_sheets(odometros: dict):
    if not odometros:
        print("⚠️  Sin datos para actualizar.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Conectando a Google Sheets...")

    creds_dict = json.loads(GOOGLE_CREDS)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(creds_dict, f)
        creds_path = f.name

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SHEET_ID)
    os.unlink(creds_path)

    total = 0
    no_encontrados = []

    for nombre in PESTANAS:
        try:
            ws    = sheet.worksheet(nombre)
            datos = ws.get_all_values()
            print(f"\n  📋 {nombre} ({len(datos)-1} filas)")

            batch = []
            for idx, fila in enumerate(datos[1:], start=2):
                if not fila or not fila[COL_PATENTE].strip():
                    continue
                patente = normalizar_patente(fila[COL_PATENTE])
                if patente in odometros:
                    from gspread.utils import rowcol_to_a1
                    km_scania    = odometros[patente]
                    km_sheet_str = fila[COL_KM_ACTUAL].strip() if len(fila) > COL_KM_ACTUAL else ""
                    km_sheet     = int(km_sheet_str.replace(".", "").replace(",", "")) if km_sheet_str.isdigit() else 0
                    if km_scania > km_sheet:
                        celda = rowcol_to_a1(idx, COL_KM_ACTUAL + 1)
                        batch.append({"range": celda, "values": [[km_scania]]})
                        print(f"    ✅ {fila[COL_PATENTE]} → {km_scania:,} km")
                        total += 1
                    else:
                        print(f"    ⏭️  {fila[COL_PATENTE]} sin cambio")
                else:
                    no_encontrados.append(f"{nombre}: {fila[COL_PATENTE]}")

            if batch:
                ws.batch_update(batch)

        except gspread.exceptions.WorksheetNotFound:
            print(f"  ❌ Pestaña '{nombre}' no encontrada")
        except Exception as e:
            print(f"  ❌ Error en '{nombre}': {e}")

    print(f"\n{'='*50}")
    print(f"✅ Scania actualizados: {total} vehículos")
    if no_encontrados:
        print(f"⚠️  No encontrados en Scania ({len(no_encontrados)}):")
        for p in no_encontrados[:5]:
            print(f"   - {p}")
    print(f"{'='*50}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  SCANIA API → GOOGLE SHEETS")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")

    odometros = extraer_odometros_scania()

    if odometros:
        actualizar_sheets(odometros)
    else:
        print("\n⚠️  Sin datos de Scania.")
