"""
scania_odometros.py
1. Login en my.scania.com (OAuth/OpenID)
2. Navega a la lista de vehículos
3. Extrae odómetro de cada vehículo
4. Actualiza columna H (KM ACTUAL) en Google Sheets
"""

import os
import re
import time
import json
import tempfile
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

import gspread
from google.oauth2.service_account import Credentials

# ─── CREDENCIALES DESDE GITHUB SECRETS ──────────────────────────────────────
SCANIA_USUARIO  = os.environ["SCANIA_USUARIO"]
SCANIA_PASSWORD = os.environ["SCANIA_PASSWORD"]
SHEET_ID        = os.environ["SHEET_ID"]
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS_JSON"]

# ─── CONFIGURACIÓN ───────────────────────────────────────────────────────────
SCANIA_LOGIN_URL = "https://my.scania.com/start"
SCANIA_FLOTA_URL = "https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list"

PESTANAS = [
    "Services-LAD",
    "Services-BUE",
    "Services-CAT",
    "Services-COR",
    "Services-LRJ",
    "Services-TUC",
]

COL_PATENTE   = 0  # Columna A
COL_KM_ACTUAL = 7  # Columna H


# ─── HELPERS ────────────────────────────────────────────────────────────────
def normalizar_patente(texto: str) -> str:
    return re.sub(r"\s+", "", str(texto)).upper().strip()

def es_patente(texto: str) -> bool:
    t = normalizar_patente(texto)
    return bool(re.match(r'^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$', t))

def parsear_km(texto: str) -> int:
    """Convierte '1.211.581 km' o '1211581' a int."""
    t = re.sub(r'[^\d]', '', texto)
    return int(t) if t and int(t) > 1000 else 0


# ─── PASO 1: LOGIN Y EXTRACCIÓN ──────────────────────────────────────────────
def extraer_odometros_scania() -> dict:
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando Chrome headless...")

    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1280,900")
    opciones.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")

    driver = webdriver.Chrome(options=opciones)
    wait   = WebDriverWait(driver, 30)
    odometros = {}

    try:
        # ── LOGIN ──────────────────────────────────────────────────────────
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Navegando a Scania...")
        driver.get(SCANIA_LOGIN_URL)
        time.sleep(5)
        print(f"URL actual: {driver.current_url}")

        # Scania redirige a mylogin.scania.com — esperar campo usuario
        selectores_usuario = [
            "input[type='text']",
            "input[type='email']",
            "input[id*='user' i]",
            "input[name*='user' i]",
            "input[id*='login' i]",
            "input[placeholder*='usuario' i]",
            "input[placeholder*='user' i]",
            "input[placeholder*='email' i]",
        ]

        campo_usuario = None
        for sel in selectores_usuario:
            try:
                campo_usuario = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                print(f"Campo usuario encontrado: {sel}")
                break
            except:
                continue

        if not campo_usuario:
            raise Exception("No se encontró campo usuario")

        # Ingresar usuario
        driver.execute_script("arguments[0].value = arguments[1];", campo_usuario, SCANIA_USUARIO)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_usuario)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_usuario)
        time.sleep(0.5)

        # Algunos portales OAuth tienen botón "Siguiente" antes de mostrar contraseña
        for sel_next in ["button[type='submit']", "input[type='submit']", "button[id*='next' i]", "button[id*='siguiente' i]"]:
            try:
                btn_next = driver.find_element(By.CSS_SELECTOR, sel_next)
                driver.execute_script("arguments[0].click();", btn_next)
                print(f"Botón siguiente: {sel_next}")
                time.sleep(3)
                break
            except:
                continue

        # Campo contraseña
        try:
            campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
            driver.execute_script("arguments[0].value = arguments[1];", campo_pass, SCANIA_PASSWORD)
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_pass)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_pass)
            time.sleep(0.5)
        except Exception as e:
            raise Exception(f"No se encontró campo contraseña: {e}")

        # Botón login final
        for sel_btn in ["button[type='submit']", "input[type='submit']", "button[id*='login' i]", "button[id*='sign' i]"]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel_btn)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Botón login: {sel_btn}")
                break
            except:
                continue

        print(f"Login enviado, esperando redirección OAuth...")
        time.sleep(10)
        print(f"Post-login URL: {driver.current_url}")

        # ── ACEPTAR COOKIES ────────────────────────────────────────────────
        # El banner de cookies bloquea la carga — hay que aceptarlo primero
        for sel_cookie in [
            "button[id*='accept' i]",
            "button[class*='accept' i]",
            "button[data-testid*='accept' i]",
            "//button[contains(text(),'Acepto')]",
            "//button[contains(text(),'Accept')]",
            "//button[contains(text(),'acepto')]",
        ]:
            try:
                if sel_cookie.startswith("//"):
                    btn_cookie = driver.find_element(By.XPATH, sel_cookie)
                else:
                    btn_cookie = driver.find_element(By.CSS_SELECTOR, sel_cookie)
                driver.execute_script("arguments[0].click();", btn_cookie)
                print(f"✅ Cookies aceptadas: {sel_cookie}")
                time.sleep(2)
                break
            except:
                continue

        # ── NAVEGAR A LISTA DE VEHÍCULOS ───────────────────────────────────
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Navegando a lista de vehículos...")
        driver.get(SCANIA_FLOTA_URL)
        time.sleep(12)  # Scania carga lento con JS

        # Aceptar cookies también en la página de flota si aparece
        for sel_cookie in [
            "//button[contains(text(),'Acepto')]",
            "//button[contains(text(),'Accept')]",
            "button[id*='accept' i]",
        ]:
            try:
                if sel_cookie.startswith("//"):
                    btn_cookie = driver.find_element(By.XPATH, sel_cookie)
                else:
                    btn_cookie = driver.find_element(By.CSS_SELECTOR, sel_cookie)
                driver.execute_script("arguments[0].click();", btn_cookie)
                print(f"✅ Cookies aceptadas en flota: {sel_cookie}")
                time.sleep(3)
                break
            except:
                pass
        print(f"URL flota: {driver.current_url}")

        # Hacer scroll para forzar carga lazy
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)

        # Imprimir texto visible para diagnóstico
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print(f"Texto visible (primeras 500 chars): {body_text[:500]}")

        # ── EXTRAER LINKS DE VEHÍCULOS ─────────────────────────────────────
        links_vehiculos = []

        # Estrategia 1: links directos a vehicle-details
        elementos = driver.find_elements(By.CSS_SELECTOR, "a[href*='vehicle-details']")
        for el in elementos:
            href = el.get_attribute("href")
            if href and "vehicle-details" in href and href not in links_vehiculos:
                links_vehiculos.append(href)
        print(f"Estrategia 1 (links directos): {len(links_vehiculos)}")

        # Estrategia 2: buscar patentes en texto y construir URLs
        if not links_vehiculos:
            # Buscar IDs de vehículos en el HTML (UUIDs)
            page_source = driver.page_source
            uuids = re.findall(r'vehicle-details/([0-9a-f-]{36})', page_source)
            uuids_unicos = list(dict.fromkeys(uuids))
            for uuid in uuids_unicos:
                url = f"https://fmp-fleetposition.cs.scania.com/vehicles/vehicle-details/{uuid}"
                if url not in links_vehiculos:
                    links_vehiculos.append(url)
            print(f"Estrategia 2 (UUIDs en HTML): {len(links_vehiculos)}")

        # Estrategia 3: buscar en elementos clickeables con patente
        if not links_vehiculos:
            todos_links = driver.find_elements(By.TAG_NAME, "a")
            for link in todos_links:
                href = link.get_attribute("href") or ""
                if "vehicle" in href.lower() and href not in links_vehiculos:
                    links_vehiculos.append(href)
                    print(f"  Link vehiculo: {href}")
            print(f"Estrategia 3 (links con vehicle): {len(links_vehiculos)}")

        if not links_vehiculos:
            print("No se encontraron vehículos — guardando HTML...")
            with open("diagnostico_scania_lista.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("HTML guardado: diagnostico_scania_lista.html")

        print(f"Total vehículos a procesar: {len(links_vehiculos)}")

        # ── EXTRAER KM DE CADA VEHÍCULO ────────────────────────────────────
        for url_vehiculo in links_vehiculos:
            try:
                driver.get(url_vehiculo)
                time.sleep(4)

                # Buscar patente en la página
                patente_encontrada = None
                page_text = driver.find_element(By.TAG_NAME, "body").text

                # Buscar patrón de patente en el texto
                matches = re.findall(r'\b[A-Z]{2}\d{3}[A-Z]{2}\b|\b[A-Z]{3}\d{3}\b', page_text)
                if matches:
                    patente_encontrada = normalizar_patente(matches[0])

                # Buscar km en el texto — formato "X.XXX.XXX km"
                km_encontrado = 0
                km_matches = re.findall(r'[\d]{1,3}(?:[\.,]\d{3})*\s*km', page_text, re.IGNORECASE)
                for km_texto in km_matches:
                    km = parsear_km(km_texto)
                    if km > km_encontrado:
                        km_encontrado = km

                # También buscar "Cuentakilómetros" específicamente
                if "Cuentakilómetros" in page_text or "cuentakilometros" in page_text.lower():
                    idx = page_text.lower().find("cuentakilómetros")
                    if idx == -1:
                        idx = page_text.lower().find("cuentakilometros")
                    fragmento = page_text[idx:idx+50]
                    km_matches2 = re.findall(r'[\d]{1,3}(?:[.,]\d{3})+', fragmento)
                    for km_txt in km_matches2:
                        km = parsear_km(km_txt)
                        if km > 1000:
                            km_encontrado = km
                            break

                if patente_encontrada and km_encontrado:
                    odometros[patente_encontrada] = km_encontrado
                    print(f"  ✅ {patente_encontrada}: {km_encontrado:,} km")
                else:
                    print(f"  ⚠️  {url_vehiculo.split('/')[-1][:8]} — patente: {patente_encontrada}, km: {km_encontrado}")

            except Exception as e:
                print(f"  ❌ Error en {url_vehiculo}: {e}")
                continue

        if not odometros:
            print(f"\n⚠️  Sin datos.")
            with open("diagnostico_scania.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

    except Exception as e:
        print(f"\n❌ Error general: {e}")
        with open("error_scania.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        raise
    finally:
        driver.quit()

    print(f"\nTotal Scania: {len(odometros)} vehículos")
    return odometros


# ─── PASO 2: ACTUALIZAR SHEETS ───────────────────────────────────────────────
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
                    km_nexpro = odometros[patente]
                    km_sheet_str = fila[COL_KM_ACTUAL].strip() if len(fila) > COL_KM_ACTUAL else ""
                    km_sheet = int(km_sheet_str.replace(".", "").replace(",", "")) if km_sheet_str.isdigit() else 0
                    if km_nexpro > km_sheet:
                        celda = rowcol_to_a1(idx, COL_KM_ACTUAL + 1)
                        batch.append({"range": celda, "values": [[km_nexpro]]})
                        print(f"    ✅ {fila[COL_PATENTE]} → {km_nexpro:,} km")
                        total += 1
                    else:
                        print(f"    ⏭️  {fila[COL_PATENTE]} sin cambio (Sheet: {km_sheet:,} ≥ Scania: {km_nexpro:,})")
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
        for p in no_encontrados[:10]:
            print(f"   - {p}")
    print(f"{'='*50}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  SCANIA → GOOGLE SHEETS")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")

    odometros = extraer_odometros_scania()

    if odometros:
        actualizar_sheets(odometros)
    else:
        print("\n❌ Sin datos de Scania.")
        exit(1)
