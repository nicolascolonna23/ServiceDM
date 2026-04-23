"""
nexpro_odometros.py
Corre en GitHub Actions: login en Nexpro → extrae km → actualiza Google Sheets
Las credenciales vienen de variables de entorno (GitHub Secrets)
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

# ─── CREDENCIALES DESDE VARIABLES DE ENTORNO (GitHub Secrets) ───────────────
NEXPRO_USUARIO  = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]
SHEET_ID        = os.environ["SHEET_ID"]
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS_JSON"]  # contenido del .json como string

# ─── CONFIGURACIÓN ───────────────────────────────────────────────────────────
NEXPRO_URL = "https://nexproconnect.net/Iveco/Login/Login2.aspx"

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

def es_km(texto: str) -> bool:
    t = texto.replace(".", "").replace(",", "").strip()
    return t.isdigit() and 1000 < int(t) < 5_000_000


# ─── PASO 1: LOGIN Y EXTRACCIÓN ──────────────────────────────────────────────
def extraer_odometros() -> dict:
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando Chrome headless...")

    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1280,900")
    opciones.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")

    driver = webdriver.Chrome(options=opciones)
    wait   = WebDriverWait(driver, 25)
    odometros = {}

    try:
        # --- Login ---
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Navegando al login...")
        driver.get(NEXPRO_URL)
        time.sleep(4)

        print(f"URL: {driver.current_url} | Título: {driver.title}")

        # Campo usuario — ASP.NET usa IDs dinámicos, probamos varios selectores
        selectores_usuario = [
            "input[type='text']",
            "input[id*='user' i]",
            "input[id*='usuario' i]",
            "input[name*='user' i]",
            "input[name*='usuario' i]",
        ]
        campo_usuario = None
        for sel in selectores_usuario:
            try:
                # element_to_be_clickable espera a que el campo esté activo y habilitado
                campo_usuario = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                print(f"Campo usuario: {sel}")
                break
            except:
                continue

        if not campo_usuario:
            raise Exception("No se encontró el campo usuario — guardá diagnostico.html para revisar")

        campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))

        # Usar JavaScript para escribir — más robusto que send_keys en portales ASP.NET
        driver.execute_script("arguments[0].value = '';", campo_usuario)
        driver.execute_script("arguments[0].value = arguments[1];", campo_usuario, NEXPRO_USUARIO)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_usuario)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_usuario)
        time.sleep(0.5)

        driver.execute_script("arguments[0].value = '';", campo_pass)
        driver.execute_script("arguments[0].value = arguments[1];", campo_pass, NEXPRO_PASSWORD)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_pass)
        driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_pass)
        time.sleep(0.5)

        print(f"Credenciales ingresadas via JavaScript")

        # Botón login
        selectores_btn = [
            "input[type='submit']",
            "button[type='submit']",
            "input[value*='Ingres' i]",
            "input[value*='Entrar' i]",
            "button[id*='login' i]",
        ]
        btn = None
        for sel in selectores_btn:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                print(f"Botón login: {sel}")
                break
            except:
                continue

        if not btn:
            raise Exception("No se encontró el botón de login")

        driver.execute_script("arguments[0].click();", btn)
        print(f"Login enviado, esperando redirección...")
        time.sleep(8)
        print(f"Post-login URL: {driver.current_url}")

        # --- Buscar sección de odómetros/flota ---
        urls_reportes = [
            "https://nexproconnect.net/Iveco/Unidades/UnidadesShowTable2.aspx",
            "https://nexproconnect.net/Iveco/MapServer/Seguimiento_V3.aspx",
            "https://nexproconnect.net/Iveco/MapServer/Seguimiento2.aspx",
            "https://nexproconnect.net/Iveco/Reportes/Scoring_UnidadesIveco.aspx",
            "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx",
            "https://nexproconnect.net/Iveco/CAN/UnidadesCAN2.aspx",
        ]

        for url in urls_reportes:
            print(f"\nProbando: {url}")
            driver.get(url)
            time.sleep(8)

            # Intentar desactivar paginación (mostrar todos los registros)
            for sel_todos in ["select[id*='size']", "select[id*='length']", "select[name*='length']"]:
                try:
                    from selenium.webdriver.support.ui import Select
                    sel_elem = driver.find_element(By.CSS_SELECTOR, sel_todos)
                    select = Select(sel_elem)
                    select.select_by_value("-1")
                    time.sleep(4)
                    print(f"  Paginación desactivada")
                    break
                except:
                    pass

            tablas = driver.find_elements(By.TAG_NAME, "table")
            if not tablas:
                print(f"  Sin tablas — guardando HTML")
                with open(f"diag_{url.split('/')[-1]}.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                continue

            print(f"  {len(tablas)} tabla(s)")

            filas_totales = 0
            for tabla in tablas:
                filas = tabla.find_elements(By.TAG_NAME, "tr")
                filas_totales += len(filas)
                for idx_fila, fila in enumerate(filas[1:]):
                    celdas = fila.find_elements(By.TAG_NAME, "td")
                    textos = [c.text.strip() for c in celdas]
                    if idx_fila < 3:
                        print(f"  DEBUG: {textos[:7]}")
                    for i, texto in enumerate(textos):
                        if es_patente(texto):
                            patente = normalizar_patente(texto)
                            # Tomar el valor numérico MÁS GRANDE (odómetro total > 10.000)
                            mejor_km = None
                            for j in range(i+1, min(i+10, len(textos))):
                                t = textos[j].replace(".", "").replace(",", "").strip()
                                if t.isdigit():
                                    val = int(t)
                                    if val > 10000:
                                        if mejor_km is None or val > mejor_km:
                                            mejor_km = val
                            if mejor_km:
                                # Solo actualizar si el valor nuevo es mayor (más confiable)
                                if patente not in odometros or mejor_km > odometros[patente]:
                                    odometros[patente] = mejor_km
                                    print(f"  ✅ {patente}: {mejor_km:,} km")

            print(f"  Filas procesadas: {filas_totales} | Acumulado: {len(odometros)} vehículos")

        # Recorrimos todas las URLs — mostrar resumen
        print(f"\nExtracción completa: {len(odometros)} vehículos en total")

        if not odometros:
            print(f"\n⚠️  Sin datos. URL final: {driver.current_url}")
            with open("diagnostico.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("HTML guardado: diagnostico.html")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        with open("error.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        raise
    finally:
        driver.quit()

    print(f"\nTotal: {len(odometros)} vehículos extraídos")
    return odometros


# ─── PASO 2: ACTUALIZAR SHEETS ───────────────────────────────────────────────
def actualizar_sheets(odometros: dict):
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
                    # Leer km actual que ya tiene el Sheet
                    km_sheet_str = fila[COL_KM_ACTUAL].strip() if len(fila) > COL_KM_ACTUAL else ""
                    km_sheet = int(km_sheet_str.replace(".", "").replace(",", "")) if km_sheet_str.isdigit() else 0
                    # Solo escribir si el valor de Nexpro es MAYOR al que ya está en el Sheet
                    if km_nexpro > km_sheet:
                        celda = rowcol_to_a1(idx, COL_KM_ACTUAL + 1)
                        batch.append({"range": celda, "values": [[km_nexpro]]})
                        print(f"    ✅ {fila[COL_PATENTE]} → {km_nexpro:,} km (antes: {km_sheet:,})")
                        total += 1
                    else:
                        print(f"    ⏭️  {fila[COL_PATENTE]} → sin cambio (Sheet: {km_sheet:,} ≥ Nexpro: {km_nexpro:,})")
                else:
                    no_encontrados.append(f"{nombre}: {fila[COL_PATENTE]}")

            if batch:
                ws.batch_update(batch)

        except gspread.exceptions.WorksheetNotFound:
            print(f"  ❌ Pestaña '{nombre}' no encontrada")
        except Exception as e:
            print(f"  ❌ Error en '{nombre}': {e}")

    print(f"\n{'='*50}")
    print(f"✅ Actualizados: {total} vehículos")
    if no_encontrados:
        print(f"⚠️  No encontrados en Nexpro ({len(no_encontrados)}):")
        for p in no_encontrados[:15]:
            print(f"   - {p}")
    print(f"{'='*50}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  NEXPRO → GOOGLE SHEETS")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")

    odometros = extraer_odometros()

    if odometros:
        actualizar_sheets(odometros)
    else:
        print("\n❌ Sin datos.")
        exit(1)
