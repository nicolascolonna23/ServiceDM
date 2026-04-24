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
    Hace login en Scania e intercepta el Bearer token de las llamadas API.
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
        # ── Login ──────────────────────────────────────────────────────────
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Navegando al login...")
        driver.get(LOGIN_URL)
        time.sleep(5)

        # Aceptar cookies
        for sel in ["//button[contains(text(),'I accept')]", "//button[contains(text(),'Acepto')]", "button[id*='accept' i]"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Cookies aceptadas")
                time.sleep(2)
                break
            except:
                pass

        # Campo usuario — usar ActionChains para simular escritura real
        campo_usuario = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='email']")))
        actions = ActionChains(driver)
        actions.click(campo_usuario)
        actions.pause(0.5)
        for char in SCANIA_USUARIO:
            actions.send_keys(char)
            actions.pause(0.03)
        actions.perform()
        time.sleep(1)

        val = campo_usuario.get_attribute('value')
        print(f"Usuario ingresado: len={len(val)}")

        # Si no se escribió nada, intentar con JS nativo
        if not val:
            driver.execute_script("""
                var el = arguments[0];
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, arguments[1]);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            """, campo_usuario, SCANIA_USUARIO)
            time.sleep(0.5)
            val = campo_usuario.get_attribute('value')
            print(f"Usuario via JS nativo: len={len(val)}")

        # Botón Continue
        for sel in ["//button[contains(text(),'Continue')]", "//button[contains(text(),'Continuar')]", "button[type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                btn.click()
                print(f"Continue clickeado")
                break
            except:
                continue
        time.sleep(8)

        # Campo contraseña
        campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        campo_pass.click()
        time.sleep(0.5)
        for char in SCANIA_PASSWORD:
            campo_pass.send_keys(char)
            time.sleep(0.03)
        time.sleep(0.5)

        # Botón login
        for sel in ["//button[contains(text(),'Log in')]", "//button[contains(text(),'Sign in')]", "button[type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                btn.click()
                print(f"Login clickeado")
                break
            except:
                continue

        print(f"Esperando redirección...")
        time.sleep(12)
        print(f"Post-login URL: {driver.current_url}")

        # ── Navegar al portal de flota para disparar las llamadas API ──────
        FLOTA_URL = "https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list"
        print(f"Navegando a flota...")
        driver.get(FLOTA_URL)
        time.sleep(10)

        # Aceptar cookies del portal de flota
        for sel in ["//button[contains(text(),'I accept')]", "//button[contains(text(),'Acepto')]"]:
            try:
                btn = driver.find_element(By.XPATH, sel)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(2)
                break
            except:
                pass

        time.sleep(5)

        # ── Interceptar el token de los logs de performance ────────────────
        print(f"Buscando token en logs de red...")
        logs = driver.get_log("performance")
        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") == "Network.requestWillBeSent":
                    headers = msg.get("params", {}).get("request", {}).get("headers", {})
                    auth = headers.get("Authorization") or headers.get("authorization", "")
                    if auth.startswith("Bearer ") and "fleetposition" in msg.get("params", {}).get("request", {}).get("url", ""):
                        token = auth.replace("Bearer ", "")
                        print(f"✅ Token interceptado ({len(token)} chars)")
                        break
            except:
                continue

        # Si no encontró token via logs, buscar via JavaScript
        if not token:
            print("Intentando capturar token via JavaScript...")
            try:
                # Acceder al localStorage/sessionStorage donde Angular guarda el token
                token_js = driver.execute_script("""
                    // Buscar en localStorage
                    for (var key in localStorage) {
                        var val = localStorage.getItem(key);
                        if (val && val.includes('eyJ')) return val;
                    }
                    // Buscar en sessionStorage
                    for (var key in sessionStorage) {
                        var val = sessionStorage.getItem(key);
                        if (val && val.includes('eyJ')) return val;
                    }
                    return null;
                """)
                if token_js:
                    # Puede venir como JSON con el token dentro
                    try:
                        data = json.loads(token_js)
                        token = data.get("access_token") or data.get("token") or token_js
                    except:
                        if token_js.startswith("eyJ"):
                            token = token_js
                    if token:
                        print(f"✅ Token via JS storage ({len(token)} chars)")
            except Exception as e:
                print(f"Error JS storage: {e}")

    except Exception as e:
        print(f"❌ Error login: {e}")
        raise
    finally:
        driver.quit()

    if not token:
        raise Exception("No se pudo obtener el token de Scania")

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
