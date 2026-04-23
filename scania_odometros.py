"""
scania_odometros.py
Usa Selenium con Xvfb (display virtual) para evitar el error de WebGL.
Navega fmp-fleetposition.cs.scania.com y extrae odómetros de cada vehículo.
"""

import os
import re
import json
import time
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

# ─── URLS ────────────────────────────────────────────────────────────────────
LOGIN_URL = "https://my.scania.com/start"
FLOTA_URL = "https://fmp-fleetposition.cs.scania.com/vehicles/vehicles-list"

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

def es_patente(texto: str) -> bool:
    t = normalizar_patente(texto)
    return bool(re.match(r'^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$', t))


# ─── EXTRACCIÓN ─────────────────────────────────────────────────────────────
def extraer_odometros_scania() -> dict:
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando Chrome con Xvfb...")

    # Con Xvfb corriendo (iniciado en el workflow), Chrome puede usar display virtual
    # Esto resuelve el error de WebGL que ocurre en headless puro
    opciones = Options()
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--window-size=1280,900")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    # Sin --headless para que use el display virtual de Xvfb
    opciones.add_argument(f"--display={os.environ.get('DISPLAY', ':99')}")

    driver = webdriver.Chrome(options=opciones)
    wait   = WebDriverWait(driver, 30)
    odometros = {}

    try:
        # ── Login ──────────────────────────────────────────────────────────
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Navegando al login...")
        driver.get(LOGIN_URL)
        time.sleep(5)
        print(f"URL: {driver.current_url}")

        # Aceptar cookies PRIMERO — bloquea el login si no se acepta
        print(f"Página de login cargada: {driver.title}")
        for sel_cookie in [
            "//button[contains(text(),'I accept')]",
            "//button[contains(text(),'Accept')]",
            "//button[contains(text(),'Acepto')]",
            "button[id*='accept' i]",
        ]:
            try:
                btn_c = driver.find_element(By.XPATH if sel_cookie.startswith("//") else By.CSS_SELECTOR, sel_cookie)
                driver.execute_script("arguments[0].click();", btn_c)
                print(f"Cookies aceptadas en login: {sel_cookie}")
                time.sleep(2)
                break
            except:
                pass

        # Guardar HTML del login para diagnóstico
        from selenium.webdriver.common.keys import Keys
        driver.save_screenshot("login_scania.png")
        with open("login_scania.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"HTML y screenshot del login guardados")

        # Verificar si hay iframes
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        print(f"Iframes encontrados: {len(iframes)}")
        for iframe in iframes:
            print(f"  iframe src: {iframe.get_attribute('src')}")

        # Imprimir todos los inputs de la página
        inputs = driver.find_elements(By.TAG_NAME, "input")
        print(f"Inputs encontrados: {len(inputs)}")
        for inp in inputs:
            print(f"  input type={inp.get_attribute('type')} id={inp.get_attribute('id')} name={inp.get_attribute('name')}")

        # Campo usuario
        campo_usuario = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text'], input[type='email']")))
        campo_usuario.click()
        time.sleep(0.5)
        campo_usuario.clear()
        campo_usuario.send_keys(SCANIA_USUARIO)
        time.sleep(1)
        val = campo_usuario.get_attribute('value')
        print(f"Usuario ingresado: '{val[:10]}...' (len={len(val)})")

        # Botón "Continue" — Scania usa ese texto, no submit genérico
        btn_clickeado = False
        for sel_btn in [
            "//button[contains(text(),'Continue')]",
            "//button[contains(text(),'Continuar')]",
            "//button[contains(text(),'Siguiente')]",
            "button[type='submit']",
            "input[type='submit']",
        ]:
            try:
                btn_next = driver.find_element(By.XPATH if sel_btn.startswith("//") else By.CSS_SELECTOR, sel_btn)
                driver.execute_script("arguments[0].click();", btn_next)
                print(f"Botón Continue clickeado: {sel_btn}")
                btn_clickeado = True
                break
            except:
                continue

        if not btn_clickeado:
            from selenium.webdriver.common.keys import Keys
            print("Botón no encontrado — usando Enter")
            campo_usuario.send_keys(Keys.RETURN)

        time.sleep(8)
        print(f"URL después de Continue: {driver.current_url}")
        print(f"Título: {driver.title}")

        # Campo contraseña — puede tardar más con Xvfb
        print(f"Buscando campo contraseña...")
        try:
            campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        except:
            time.sleep(5)
            campo_pass = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))

        campo_pass.click()
        time.sleep(0.5)
        campo_pass.clear()
        campo_pass.send_keys(SCANIA_PASSWORD)
        time.sleep(1)
        print(f"Contraseña ingresada")

        # Botón login final
        btn_login_clickeado = False
        for sel_login in [
            "//button[contains(text(),'Log in')]",
            "//button[contains(text(),'Sign in')]",
            "//button[contains(text(),'Iniciar')]",
            "button[type='submit']",
        ]:
            try:
                btn_login = driver.find_element(By.XPATH if sel_login.startswith("//") else By.CSS_SELECTOR, sel_login)
                btn_login.click()
                print(f"Botón login: {sel_login}")
                btn_login_clickeado = True
                break
            except:
                continue

        if not btn_login_clickeado:
            campo_pass.send_keys(Keys.RETURN)
            print("Enter en contraseña")

        print(f"Login enviado, esperando redirección...")
        time.sleep(12)
        print(f"Post-login URL: {driver.current_url}")

        # ── Aceptar cookies ────────────────────────────────────────────────
        for sel in ["//button[contains(text(),'Acepto')]", "//button[contains(text(),'Accept')]", "button[id*='accept' i]"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                print(f"Cookies aceptadas")
                time.sleep(2)
                break
            except:
                pass

        # ── Navegar a lista de vehículos ───────────────────────────────────
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Navegando a lista de vehículos...")
        driver.get(FLOTA_URL)
        time.sleep(15)  # Más tiempo para WebGL con Xvfb

        # Aceptar cookies si aparecen de nuevo
        for sel in ["//button[contains(text(),'Acepto')]", "button[id*='accept' i]"]:
            try:
                btn = driver.find_element(By.XPATH if sel.startswith("//") else By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                break
            except:
                pass

        print(f"URL flota: {driver.current_url}")
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print(f"Texto visible (500 chars): {body_text[:500]}")

        # ── Extraer links de vehículos ─────────────────────────────────────
        links = []

        # Estrategia 1: links directos
        elementos = driver.find_elements(By.CSS_SELECTOR, "a[href*='vehicle-details']")
        for el in elementos:
            href = el.get_attribute("href") or ""
            if href and href not in links:
                links.append(href)
        print(f"Links directos: {len(links)}")

        # Estrategia 2: UUIDs en el HTML
        if not links:
            uuids = re.findall(r'vehicle-details/([0-9a-f-]{36})', driver.page_source)
            uuids_unicos = list(dict.fromkeys(uuids))
            for uuid in uuids_unicos:
                url = f"https://fmp-fleetposition.cs.scania.com/vehicles/vehicle-details/{uuid}"
                if url not in links:
                    links.append(url)
            print(f"UUIDs en HTML: {len(links)}")

        # Estrategia 3: buscar via JavaScript
        if not links:
            try:
                uuids_js = driver.execute_script("""
                    var uuids = [];
                    var regex = /vehicle-details\\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/g;
                    var text = document.documentElement.innerHTML;
                    var match;
                    while ((match = regex.exec(text)) !== null) {
                        if (!uuids.includes(match[1])) uuids.push(match[1]);
                    }
                    return uuids;
                """)
                for uuid in (uuids_js or []):
                    url = f"https://fmp-fleetposition.cs.scania.com/vehicles/vehicle-details/{uuid}"
                    if url not in links:
                        links.append(url)
                print(f"UUIDs via JS: {len(links)}")
            except Exception as e:
                print(f"Error JS: {e}")

        if not links:
            with open("diagnostico_scania.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("Sin links — HTML guardado para diagnóstico")

        print(f"Total vehículos a procesar: {len(links)}")

        # ── Extraer km de cada vehículo ────────────────────────────────────
        for url in links:
            try:
                driver.get(url)
                time.sleep(5)

                body = driver.find_element(By.TAG_NAME, "body").text

                # Buscar patente
                patente = None
                matches = re.findall(r'\b[A-Z]{2}\d{3}[A-Z]{2}\b|\b[A-Z]{3}\d{3}\b', body)
                if matches:
                    patente = normalizar_patente(matches[0])

                # Buscar km — formato "1.211.581 km" o "Cuentakilómetros X"
                km = 0
                # Buscar después de "Cuentakilómetros"
                match_ck = re.search(r'Cuentakil[oó]metros\s*([\d.,]+)', body, re.IGNORECASE)
                if match_ck:
                    km = int(re.sub(r'[^\d]', '', match_ck.group(1)))

                # Buscar número grande seguido de km
                if not km:
                    km_matches = re.findall(r'([\d]{1,3}(?:[.,]\d{3})+)\s*km', body, re.IGNORECASE)
                    for km_txt in km_matches:
                        val = int(re.sub(r'[^\d]', '', km_txt))
                        if val > 10000 and val > km:
                            km = val

                if patente and km:
                    odometros[patente] = km
                    print(f"  ✅ {patente}: {km:,} km")
                else:
                    print(f"  ⚠️  {url[-8:]}: patente={patente}, km={km}")
                    print(f"      Texto: {body[:200]}")

            except Exception as e:
                print(f"  ❌ Error: {e}")
                continue

    except Exception as e:
        print(f"\n❌ Error general: {e}")
        with open("error_scania.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        raise
    finally:
        driver.quit()

    print(f"\nTotal Scania: {len(odometros)} vehículos")
    return odometros


# ─── ACTUALIZAR SHEETS ───────────────────────────────────────────────────────
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
        print(f"⚠️  No encontrados ({len(no_encontrados)}):")
        for p in no_encontrados[:10]:
            print(f"   - {p}")
    print(f"{'='*50}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  SCANIA SELENIUM → GOOGLE SHEETS")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")

    odometros = extraer_odometros_scania()

    if odometros:
        actualizar_sheets(odometros)
    else:
        print("\n⚠️  Sin datos de Scania.")
