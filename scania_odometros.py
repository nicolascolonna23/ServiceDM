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
    """
    Hace login en Scania via Keycloak directo (sin portal web)
    e intercepta el Bearer token.
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando Chrome con Xvfb...")

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
        # ── Login directo via Keycloak ─────────────────────────────────────
        # Navegar directamente a la URL de login de Keycloak con el cliente correcto
        LOGIN_KEYCLOAK = "https://mylogin.scania.com/auth/realms/fg-ext/protocol/openid-connect/auth?client_id=cs_fmp_fleetposition_app&redirect_uri=https%3A%2F%2Ffmp-fleetposition.cs.scania.com%2F&response_type=code&scope=openid"
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Navegando a Keycloak login...")
        driver.get(LOGIN_KEYCLOAK)
        time.sleep(5)
        print(f"URL: {driver.current_url}")
        print(f"Título: {driver.title}")

        # Guardar screenshot para diagnóstico
        driver.save_screenshot("login_keycloak.png")

        # Aceptar cookies si aparecen
        for sel in ["//button[contains(text(),'I accept')]", "//button[contains(text(),'Acepto')]", "button[id*='accept' i]"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Cookies aceptadas")
                time.sleep(2)
                break
            except:
                pass

        # Imprimir todos los inputs disponibles
        inputs = driver.find_elements(By.TAG_NAME, "input")
        print(f"Inputs en la página: {len(inputs)}")
        for inp in inputs:
            print(f"  type={inp.get_attribute('type')} id={inp.get_attribute('id')} name={inp.get_attribute('name')} class={inp.get_attribute('class')[:30] if inp.get_attribute('class') else ''}")

        # Intentar escribir en el campo usuario con múltiples estrategias
        campo_usuario = None
        for sel in ["input[name='email']", "input[type='text']", "input[type='email']", "#username", "#email"]:
            try:
                campo_usuario = driver.find_element(By.CSS_SELECTOR, sel)
                print(f"Campo encontrado: {sel}")
                break
            except:
                continue

        if campo_usuario:
            # Hacer scroll al elemento y hacer click
            driver.execute_script("arguments[0].scrollIntoView(true);", campo_usuario)
            time.sleep(0.3)
            
            # Click via JavaScript para asegurar foco
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_usuario)
            time.sleep(0.5)
            
            # Escribir via JavaScript disparando eventos de teclado individuales
            script_escribir = """
                var el = arguments[0];
                var texto = arguments[1];
                el.focus();
                for (var i = 0; i < texto.length; i++) {
                    var char = texto[i];
                    var keyDown = new KeyboardEvent('keydown', {key: char, bubbles: true});
                    var keyPress = new KeyboardEvent('keypress', {key: char, bubbles: true});
                    var keyUp = new KeyboardEvent('keyup', {key: char, bubbles: true});
                    el.dispatchEvent(keyDown);
                    el.dispatchEvent(keyPress);
                    // Modificar valor
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, el.value + char);
                    el.dispatchEvent(new InputEvent('input', {bubbles: true, data: char}));
                    el.dispatchEvent(keyUp);
                }
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.value;
            """
            val = driver.execute_script(script_escribir, campo_usuario, SCANIA_USUARIO)
            print(f"Usuario via KeyboardEvents: '{val[:10] if val else ''}' len={len(val) if val else 0}")

        # Tomar screenshot del estado actual
        driver.save_screenshot("after_user_input.png")

        # Click en Continue
        for sel in ["//button[contains(text(),'Continue')]", "//button[contains(text(),'Continuar')]", "button[type='submit']", "input[type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Continue: {sel}")
                break
            except:
                continue

        time.sleep(8)
        print(f"URL post-Continue: {driver.current_url}")
        driver.save_screenshot("after_continue.png")

        # Campo contraseña
        try:
            campo_pass = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
            driver.execute_script("arguments[0].click(); arguments[0].focus();", campo_pass)
            time.sleep(0.3)
            val_pass = driver.execute_script(script_escribir, campo_pass, SCANIA_PASSWORD)
            print(f"Password: len={len(val_pass) if val_pass else 0}")

            # Login
            for sel in ["//button[contains(text(),'Log in')]", "//button[contains(text(),'Sign in')]", "button[type='submit']"]:
                try:
                    btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                    driver.execute_script("arguments[0].click();", btn)
                    print(f"Login: {sel}")
                    break
                except:
                    continue

            print(f"Esperando redirección...")
            time.sleep(15)
            print(f"URL final: {driver.current_url}")

        except Exception as e:
            print(f"Error en contraseña: {e}")
            driver.save_screenshot("error_pass.png")
            raise

        # ── Interceptar token de los logs de performance ───────────────────
        print(f"Buscando token en logs...")
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") == "Network.requestWillBeSent":
                    req = msg.get("params", {}).get("request", {})
                    headers = req.get("headers", {})
                    url = req.get("url", "")
                    auth = headers.get("Authorization") or headers.get("authorization", "")
                    if auth.startswith("Bearer ") and ("fleetposition" in url or "scania" in url):
                        token = auth.replace("Bearer ", "")
                        print(f"✅ Token interceptado de {url[:60]}")
                        break
            except:
                continue

        if not token:
            print("Token no encontrado en logs de red")
            # Navegar al portal para generar más requests
            driver.get("https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list")
            time.sleep(8)
            logs2 = driver.get_log("performance")
            for entry in logs2:
                try:
                    msg = json.loads(entry["message"])["message"]
                    if msg.get("method") == "Network.requestWillBeSent":
                        req = msg.get("params", {}).get("request", {})
                        headers = req.get("headers", {})
                        url = req.get("url", "")
                        auth = headers.get("Authorization") or headers.get("authorization", "")
                        if auth.startswith("Bearer ") and "fleetposition" in url:
                            token = auth.replace("Bearer ", "")
                            print(f"✅ Token interceptado (2do intento)")
                            break
                except:
                    continue

    except Exception as e:
        print(f"❌ Error: {e}")
        try:
            driver.save_screenshot("error_scania.png")
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
