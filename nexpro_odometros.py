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
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS_JSON"]

# FIX: parsear SHEET_ID tanto si es URL completa como si es solo el ID
_sheet_raw = os.environ["SHEET_ID"]
_m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _sheet_raw)
SHEET_ID = _m.group(1) if _m else _sheet_raw.strip()

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
                campo_usuario = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                print(f"Campo usuario: {sel}")
                break
            except:
                continue
        if not campo_usuario:
            raise Exception("No se encontró el campo usuario")

        campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))

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

            from selenium.webdriver.support.ui import Select
            selectores_paginacion = [
                "select",
                "select[id*='size']",
                "select[id*='length']",
                "select[id*='registros']",
                "select[name*='length']",
                "select[name*='size']",
            ]
            paginacion_desactivada = False
            for sel_pag in selectores_paginacion:
                try:
                    elementos = driver.find_elements(By.CSS_SELECTOR, sel_pag)
                    for sel_elem in elementos:
                        select = Select(sel_elem)
                        opciones = [o.get_attribute("value") for o in select.options]
                        print(f"  Select encontrado — opciones: {opciones}")
                        if "-1" in opciones:
                            select.select_by_value("-1")
                            paginacion_desactivada = True
                        elif opciones:
                            nums = [(int(v), v) for v in opciones if v.lstrip("-").isdigit()]
                            if nums:
                                valor_max = max(nums, key=lambda x: x[0])[1]
                                select.select_by_value(valor_max)
                                paginacion_desactivada = True
                        if paginacion_desactivada:
                            print(f"  ✅ Paginación ajustada")
                            time.sleep(4)
                            break
                except Exception as ep:
                    print(f"  Select error: {ep}")
                    continue
                if paginacion_desactivada:
                    break

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
                            mejor_km = None
                            for j in range(i+1, min(i+10, len(textos))):
                                t = textos[j].replace(".", "").replace(",", "").strip()
                                if t.isdigit():
                                    val = int(t)
                                    if val > 10000:
                                        if mejor_km is None or val > mejor_km:
                                            mejor_km = val
                            if mejor_km:
                                if patente not in odometros or mejor_km > odometros[patente]:
                                    odometros[patente] = mejor_km
                                    print(f"  ✅ {patente}: {mejor_km:,} km")
            print(f"  Filas procesadas: {filas_totales} | Acumulado: {len(odometros)} vehículos")

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
def conectar_sheets():
    """
    Autenticación con Google Sheets.
    Usa service_account_from_dict (el método más confiable de gspread).
    """
    creds_dict = json.loads(GOOGLE_CREDS)

    # Diagnóstico: mostrar qué cuenta y qué ID se está usando
    print(f"  Service account : {creds_dict.get('client_email', 'NO ENCONTRADO')}")
    print(f"  SHEET_ID        : '{SHEET_ID}'")

    # service_account_from_dict maneja scopes y refresh internamente
    gc = gspread.service_account_from_dict(creds_dict)
    return gc.open_by_key(SHEET_ID)


def actualizar_sheets(odometros: dict):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Conectando a Google Sheets...")

    sheet = None
    for intento in range(1, 4):
        try:
            sheet = conectar_sheets()
            print(f"  ✅ Conectado al spreadsheet")
            break
        except gspread.exceptions.APIError as e:
            # Mostrar el status HTTP real para diagnosticar el problema
            status = getattr(e.response, "status_code", "?")
            body   = getattr(e.response, "text", "")[:300]
            print(f"  ❌ Intento {intento}/3 — HTTP {status}: {body}")
            if intento < 3:
                time.sleep(10)
            else:
                raise
        except Exception as e:
            print(f"  ❌ Intento {intento}/3 — {type(e).__name__}: {e}")
            if intento < 3:
                time.sleep(10)
            else:
                raise

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
                    km_sheet_clean = km_sheet_str.replace(".", "").replace(",", "")
                    km_sheet = int(km_sheet_clean) if km_sheet_clean.isdigit() else 0
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
