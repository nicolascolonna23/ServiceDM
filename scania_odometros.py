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
    """Usa Playwright para login en Scania — maneja shadow DOM nativamente."""
    from playwright.sync_api import sync_playwright

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando Playwright...")
    token = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )
        page = context.new_page()

        try:
            # Interceptar requests para capturar el token
            captured_token = []
            def on_request(request):
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and "fleetposition" in request.url:
                    if not captured_token:
                        captured_token.append(auth.replace("Bearer ", ""))
                        print(f"✅ Token interceptado de {request.url[:60]}")

            context.on("request", on_request)

            # Login
            print(f"Navegando a my.scania.com...")
            page.goto("https://my.scania.com/start", wait_until="networkidle", timeout=30000)
            print(f"URL: {page.url}")

            # Aceptar cookies
            try:
                page.click("text=I accept", timeout=5000)
                print("Cookies aceptadas")
                page.wait_for_timeout(2000)
            except:
                pass

            # Campo email — Playwright maneja shadow DOM con pierce selector
            print("Buscando campo email...")
            try:
                # Playwright usa >> para shadow DOM piercing
                email_field = page.locator("input[name='email']")
                email_field.wait_for(timeout=10000)
                email_field.fill(SCANIA_USUARIO)
                val = email_field.input_value()
                print(f"Email ingresado: len={len(val)}")
            except Exception as e:
                print(f"Error email: {e}")
                # Intentar con pierce
                try:
                    page.evaluate(f"""
                        const input = document.querySelector('input[name="email"]') ||
                                      [...document.querySelectorAll('*')]
                                          .map(el => el.shadowRoot)
                                          .filter(Boolean)
                                          .flatMap(sr => [...sr.querySelectorAll('input[name="email"]')])[0];
                        if (input) {{
                            input.focus();
                            input.value = '{SCANIA_USUARIO}';
                            input.dispatchEvent(new Event('input', {{bubbles: true}}));
                            input.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    """)
                except:
                    pass

            # Continue
            try:
                page.click("text=Continue", timeout=5000)
                print("Continue clickeado")
            except:
                page.keyboard.press("Enter")
            page.wait_for_timeout(6000)
            print(f"URL post-Continue: {page.url}")

            # Password
            try:
                pass_field = page.locator("input[type='password']")
                pass_field.wait_for(timeout=10000)
                pass_field.fill(SCANIA_PASSWORD)
                print(f"Password ingresado")
            except Exception as e:
                print(f"Error password: {e}")

            # Login
            try:
                page.click("text=Log in", timeout=3000)
            except:
                try:
                    page.click("button[type='submit']", timeout=3000)
                except:
                    page.keyboard.press("Enter")

            print("Esperando redirección...")
            page.wait_for_timeout(12000)
            print(f"URL final: {page.url}")

            # Navegar al portal para disparar requests con token
            if not captured_token:
                print("Navegando al portal de flota...")
                page.goto("https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list",
                          wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(8000)

            if captured_token:
                token = captured_token[0]
            else:
                print("Token no capturado — guardando screenshot")
                page.screenshot(path="debug_playwright.png")

        except Exception as e:
            print(f"❌ Error Playwright: {e}")
            try:
                page.screenshot(path="error_playwright.png")
            except:
                pass
            raise
        finally:
            browser.close()

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
