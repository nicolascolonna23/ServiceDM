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
                campo_usuario = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                print(f"Campo usuario: {sel}")
                break
            except:
                continue

        if not campo_usuario:
            raise Exception("No se encontró el campo usuario — guardá diagnostico.html para revisar")

        campo_pass = driver.find_element(By.CSS_SELECTOR, "input[type='password']")

        campo_usuario.clear()
        campo_usuario.send_keys(NEXPRO_USUARIO)
        time.sleep(0.5)
        campo_pass.clear()
        campo_pass.send_keys(NEXPRO_PASSWORD)
        time.sleep(0.5)

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

        btn.click()
        print(f"Login enviado, esperando redirección...")
        time.sleep(6)
        print(f"Post-login URL: {driver.current_url}")

        # --- Buscar sección de odómetros/flota ---
        urls_reportes = [
            "https://nexproconnect.net/Iveco/Reportes/VehicleRanking.aspx",
            "https://nexproconnect.net/Iveco/Reportes/PosicionFlota.aspx",
            "https://nexproconnect.net/Iveco/Reportes/Flota.aspx",
            "https://nexproconnect.net/Iveco/Mapa/Mapa.aspx",
        ]

        for url in urls_reportes:
            print(f"\nProbando: {url}")
            driver.get(url)
            time.sleep(5)

            tablas = driver.find_elements(By.TAG_NAME, "table")
            if not tablas:
                print(f"  Sin tablas")
                continue

            print(f"  {len(tablas)} tabla(s)")

            for tabla in tablas:
                filas = tabla.find_elements(By.TAG_NAME, "tr")
                for fila in filas[1:]:
                    celdas = fila.find_elements(By.TAG_NAME, "td")
                    textos = [c.text.strip() for c in celdas]
                    for i, texto in enumerate(textos):
                        if es_patente(texto):
                            patente = normalizar_patente(texto)
                            for j in range(i+1, min(i+8, len(textos))):
                                if es_km(textos[j]):
                                    km = int(textos[j].replace(".", "").replace(",", ""))
                                    odometros[patente] = km
                                    print(f"  ✅ {patente}: {km:,} km")
                                    break

            if odometros:
                print(f"Extracción exitosa.")
                break

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
                    celda = rowcol_to_a1(idx, COL_KM_ACTUAL + 1)
                    batch.append({"range": celda, "values": [[odometros[patente]]]})
                    print(f"    ✅ {fila[COL_PATENTE]} → {odometros[patente]:,}")
                    total += 1
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
