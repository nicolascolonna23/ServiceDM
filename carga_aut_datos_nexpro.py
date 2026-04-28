# carga_aut_datos_nexpro.py
# VERSION FINAL v2
# TELEMETRIA + DATOS UNIDADES (ralenti, hs motor, kg CO2)

import os
import re
import json
import time
import tempfile
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =====================================================
# VARIABLES
# =====================================================
NEXPRO_USUARIO  = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]
GOOGLE_CREDS    = os.environ["GOOGLE_CREDENTIALS_JSON"]
_sheet_raw      = os.environ["SHEET_ID"]
m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _sheet_raw)
SHEET_ID = m.group(1) if m else _sheet_raw.strip()

URL_LOGIN       = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
URL_REPORTE     = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"
URL_PERFORMANCE = "https://nexproconnect.net/Iveco/Reportes/Scoring_UnidadesIveco.aspx"

HOJA_TELEMETRIA = "TELEMETRIA"
HOJA_DATOS      = "DATOS UNIDADES"

# Patentes excluidas — no se cargan en ninguna hoja
PATENTES_EXCLUIDAS = {"AF310TU", "AE527FA"}

# =====================================================
# HELPERS
# =====================================================
def num(txt):
    txt = str(txt).strip().replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except:
        return 0.0

def obtener_mes_anterior():
    hoy = date.today()
    primero_actual  = hoy.replace(day=1)
    ultimo_anterior = primero_actual - timedelta(days=1)
    primero_anterior = ultimo_anterior.replace(day=1)
    return (
        primero_anterior.strftime("%d/%m/%Y"),   # desde
        ultimo_anterior.strftime("%d/%m/%Y"),    # hasta
        ultimo_anterior.strftime("%d/%m/%Y")     # fecha_carga = ultimo dia del mes anterior
    )

def es_patente(txt):
    return bool(re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", txt.upper().strip()))

# =====================================================
# CHROME
# =====================================================
def crear_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1400")
    return webdriver.Chrome(options=options)

# =====================================================
# LOGIN
# =====================================================
def login(driver):
    wait = WebDriverWait(driver, 30)
    driver.get(URL_LOGIN)
    time.sleep(5)
    user = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")))
    pwd  = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']")))
    driver.execute_script("arguments[0].value='';", user)
    driver.execute_script("arguments[0].value='';", pwd)
    user.send_keys(NEXPRO_USUARIO)
    pwd.send_keys(NEXPRO_PASSWORD)
    pwd.send_keys(Keys.ENTER)
    time.sleep(8)
    print("URL POST LOGIN:", driver.current_url)

# =====================================================
# APLICAR FILTRO HISTORICO + FECHAS
# (reutilizable para cualquier pantalla)
# =====================================================
def aplicar_historico(driver, desde, hasta):
    time.sleep(10)

    # --- Diagnóstico: listar todos los textos de botones/links ---
    todos = driver.find_elements(By.XPATH, "//button | //a | //span | //div[@role='button'] | //li")
    textos_encontrados = []
    for e in todos:
        try:
            t = e.text.strip()
            if t:
                textos_encontrados.append(t)
        except:
            pass
    print("Elementos clickeables en página:", textos_encontrados[:30])

    # --- Click en "Histórico" (flexible: acepta cualquier texto que contenga "hist") ---
    historico = None
    for e in todos:
        try:
            txt = e.text.strip().lower()
            if "hist" in txt:
                historico = e
                print("Encontrado Histórico:", txt)
                break
        except:
            pass

    if historico is not None:
        driver.execute_script("arguments[0].click();", historico)
        print("Click Histórico")
        time.sleep(5)
    else:
        # La pantalla no tiene botón Histórico → intentar setear fechas directamente
        print("[!] Botón Histórico no encontrado. Intentando setear fechas directamente...")

    # --- Inputs de fecha visibles y editables ---
    inputs = driver.find_elements(By.TAG_NAME, "input")
    fechas = []
    for i in inputs:
        try:
            val     = str(i.get_attribute("value") or "")
            tipo    = str(i.get_attribute("type") or "text").lower()
            visible = i.is_displayed()
            enabled = i.is_enabled()
            # Aceptar inputs con "/" en el valor, o inputs de tipo date/text visibles
            if visible and enabled and ("/" in val or tipo in ["date"]):
                fechas.append(i)
        except:
            pass

    # Fallback: buscar cualquier input text visible si no se encontraron fechas
    if len(fechas) < 2:
        print("  Buscando inputs texto genéricos...")
        for i in inputs:
            try:
                tipo    = str(i.get_attribute("type") or "text").lower()
                visible = i.is_displayed()
                enabled = i.is_enabled()
                if visible and enabled and tipo in ["text", "date"] and i not in fechas:
                    fechas.append(i)
            except:
                pass

    print("Fechas utilizables:", len(fechas))
    if len(fechas) < 2:
        raise Exception("No encontró inputs de fecha editables en esta pantalla")

    driver.execute_script("arguments[0].removeAttribute('readonly');", fechas[0])
    driver.execute_script("arguments[0].removeAttribute('readonly');", fechas[1])
    driver.execute_script("arguments[0].value=arguments[1];", fechas[0], desde)
    driver.execute_script("arguments[0].value=arguments[1];", fechas[1], hasta)
    # Disparar eventos change/blur para que la página reconozca el cambio
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", fechas[0])
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", fechas[1])
    print("Fechas seteadas:", desde, "->", hasta)

    # --- Click en "Visualizar" o "Buscar" ---
    candidatos = driver.find_elements(By.XPATH, "//button | //a | //span | //div[@role='button'] | //input[@type='button'] | //input[@type='submit']")
    for e in candidatos:
        try:
            txt = (e.text or e.get_attribute("value") or "").strip().lower()
            if any(k in txt for k in ["visualizar", "buscar", "consultar", "search", "ver"]):
                driver.execute_script("arguments[0].click();", e)
                print("Click Visualizar/Buscar:", txt)
                break
        except:
            pass
    time.sleep(14)

# =====================================================
# EXTRAER TELEMETRIA  (km, litros, l/100km)
# =====================================================
def extraer_tabla():
    desde, hasta, fecha_carga = obtener_mes_anterior()
    print("\n=== NEXPRO TELEMETRIA ===")
    print(datetime.now())
    print("Buscando:", desde, "->", hasta)

    driver = crear_driver()
    filas_finales = []
    try:
        login(driver)
        driver.get(URL_REPORTE)
        time.sleep(8)
        aplicar_historico(driver, desde, hasta)

        tablas = driver.find_elements(By.TAG_NAME, "table")
        print("Tablas encontradas:", len(tablas))

        for tabla in tablas:
            filas = tabla.find_elements(By.TAG_NAME, "tr")
            for fila in filas:
                celdas = fila.find_elements(By.TAG_NAME, "td")
                textos = [x.text.strip() for x in celdas if x.text.strip()]
                if len(textos) >= 9 and es_patente(textos[0]):
                    patente = textos[0].upper().strip()
                    if patente in PATENTES_EXCLUIDAS:
                        continue
                    litros = num(textos[3])
                    km     = num(textos[4])
                    l100   = num(textos[8])
                    filas_finales.append([
                        fecha_carga,
                        patente,
                        "", "",
                        km, litros, l100
                    ])

        print("Filas detectadas:", len(filas_finales))
        return filas_finales
    finally:
        driver.quit()

# =====================================================
# EXTRAER RALENTI del reporte de consumo
# (ya cargado en el driver, tabla visible)
# =====================================================
def extraer_ralenti_de_tabla(driver):
    """
    Lee el ralentí (lts) de la tabla de consumo ya visualizada.
    Intenta detectar la columna por el header; si no la encuentra
    usa un índice fijo configurable.
    Retorna dict: { "PATENTE": ralenti_float }
    """
    datos = {}
    tablas = driver.find_elements(By.TAG_NAME, "table")

    for tabla in tablas:
        # Detectar headers (th o primera fila de td)
        ths = tabla.find_elements(By.TAG_NAME, "th")
        if ths:
            headers = [h.text.strip().lower() for h in ths]
        else:
            primera = tabla.find_elements(By.TAG_NAME, "tr")
            headers = []
            if primera:
                headers = [c.text.strip().lower()
                           for c in primera[0].find_elements(By.TAG_NAME, "td")]

        print("Headers consumo:", headers)

        # Buscar índice de ralentí
        # Preferir "consumo en ralentí" (lts) sobre "% de ralentí"
        idx_ralenti  = None
        idx_dominio  = 0
        for i, h in enumerate(headers):
            if any(k in h for k in ["ralenti", "ralentí", "idle", "relenti"]):
                # Solo tomar el primero que matchee (consumo en lts, no el %)
                # Si ya encontramos uno y este tiene "%" en el nombre, ignorarlo
                if idx_ralenti is None or "%" not in h:
                    idx_ralenti = i
                    print(f"  → Ralentí en columna {i}: '{h}'")
            if any(k in h for k in ["patente", "dominio", "unidad", "placa"]):
                idx_dominio = i

        # Si no hay header con ralentí, saltar esta tabla
        if idx_ralenti is None:
            continue

        filas = tabla.find_elements(By.TAG_NAME, "tr")
        for fila in filas:
            celdas = fila.find_elements(By.TAG_NAME, "td")
            textos = [c.text.strip() for c in celdas]
            if len(textos) <= max(idx_dominio, idx_ralenti):
                continue
            dominio = textos[idx_dominio].upper().strip()
            if es_patente(dominio) and dominio not in PATENTES_EXCLUIDAS:
                datos[dominio] = num(textos[idx_ralenti])

        if datos:
            break   # tabla correcta encontrada

    # --- Fallback: índice fijo si no se detectó por header ---
    if not datos:
        print("  [!] Ralentí no detectado por header. Usando índice fijo = 6")
        print("      (columna 6 = 'consumo en ralentí' según los headers detectados)")
        IDX_RALENTI = 6   # consumo en ralentí (lts), NO el % que está en columna 7
        for tabla in tablas:
            filas = tabla.find_elements(By.TAG_NAME, "tr")
            for fila in filas:
                celdas = fila.find_elements(By.TAG_NAME, "td")
                textos = [c.text.strip() for c in celdas if c.text.strip()]
                if len(textos) >= 9 and es_patente(textos[0]):
                    try:
                        p = textos[0].upper().strip()
                        if p not in PATENTES_EXCLUIDAS:
                            datos[p] = num(textos[IDX_RALENTI])
                    except:
                        pass
            if datos:
                break

    print(f"Ralentí extraído: {len(datos)} unidades")
    return datos

# =====================================================
# EXTRAER PERFORMANCE  (hs motor, kg CO2)
# =====================================================
def extraer_performance(driver, desde, hasta):
    """
    Navega al reporte de performance, aplica el mismo filtro y extrae
    hs motor y kg CO2.
    Retorna dict: { "PATENTE": {"hs_motor": float, "co2": float} }
    """
    driver.get(URL_PERFORMANCE)
    time.sleep(8)
    aplicar_historico(driver, desde, hasta)

    datos = {}
    tablas = driver.find_elements(By.TAG_NAME, "table")
    print(f"Tablas performance encontradas: {len(tablas)}")

    for tabla in tablas:
        # Detectar headers
        ths = tabla.find_elements(By.TAG_NAME, "th")
        if ths:
            headers = [h.text.strip().lower() for h in ths]
        else:
            primera = tabla.find_elements(By.TAG_NAME, "tr")
            headers = []
            if primera:
                headers = [c.text.strip().lower()
                           for c in primera[0].find_elements(By.TAG_NAME, "td")]

        print("Headers performance:", headers)

        idx_dominio = 0
        idx_motor   = None
        idx_co2     = None

        for i, h in enumerate(headers):
            if any(k in h for k in ["patente", "dominio", "unidad", "placa", "vehiculo"]):
                idx_dominio = i
            if any(k in h for k in ["motor", "hora", " hs", "h.motor", "tiempo"]):
                if idx_motor is None:   # tomar el primero que matchee
                    idx_motor = i
                    print(f"  → Hs Motor en columna {i}: '{h}'")
            if any(k in h for k in ["co2", "emisión", "emision", "emission", "carbono"]):
                idx_co2 = i
                print(f"  → CO2 en columna {i}: '{h}'")

        if idx_motor is None and idx_co2 is None:
            continue   # esta tabla no es la correcta

        filas = tabla.find_elements(By.TAG_NAME, "tr")
        for fila in filas:
            celdas = fila.find_elements(By.TAG_NAME, "td")
            textos = [c.text.strip() for c in celdas]
            if not textos:
                continue
            if len(textos) <= idx_dominio:
                continue
            dominio = textos[idx_dominio].upper().strip()
            if not es_patente(dominio) or dominio in PATENTES_EXCLUIDAS:
                continue

            hs_motor = num(textos[idx_motor]) if idx_motor is not None and len(textos) > idx_motor else 0.0
            co2      = num(textos[idx_co2])   if idx_co2  is not None and len(textos) > idx_co2  else 0.0
            datos[dominio] = {"hs_motor": hs_motor, "co2": co2}

        if datos:
            break

    print(f"Performance extraída: {len(datos)} unidades")
    return datos

# =====================================================
# EXTRAER DATOS UNIDADES  (orquesta todo)
# =====================================================
def extraer_datos_unidades():
    desde, hasta, fecha_carga = obtener_mes_anterior()
    print("\n=== NEXPRO DATOS UNIDADES ===")
    print(datetime.now())
    print("Buscando:", desde, "->", hasta)

    driver = crear_driver()
    try:
        login(driver)

        # 1) Ralentí → reporte de consumo
        driver.get(URL_REPORTE)
        time.sleep(8)
        aplicar_historico(driver, desde, hasta)
        ralenti_dict = extraer_ralenti_de_tabla(driver)

        # 2) Hs Motor + CO2 → reporte de performance
        performance_dict = extraer_performance(driver, desde, hasta)

        # 3) Combinar por dominio (excluir patentes ignoradas)
        dominios = sorted(
            d for d in set(list(ralenti_dict.keys()) + list(performance_dict.keys()))
            if d not in PATENTES_EXCLUIDAS
        )
        filas = []
        for dominio in dominios:
            ralenti  = ralenti_dict.get(dominio, 0.0)
            perf     = performance_dict.get(dominio, {})
            hs_motor = perf.get("hs_motor", 0.0)
            co2      = perf.get("co2", 0.0)
            filas.append({
                "fecha":    fecha_carga,
                "dominio":  dominio,
                "hs_motor": hs_motor,
                "ralenti":  ralenti,
                "co2":      co2,
            })

        print(f"Total unidades combinadas: {len(filas)}")
        return filas
    finally:
        driver.quit()

# =====================================================
# GOOGLE SHEETS – conexión
# =====================================================
def conectar_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(creds_dict, f)
        path = f.name
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_file(path, scopes=scopes)
    client = gspread.authorize(creds)
    os.unlink(path)
    return client.open_by_key(SHEET_ID)

def ya_existe_mes(ws, fecha):
    col = ws.col_values(1)
    return any(fecha in str(x) for x in col)

# =====================================================
# SUBIR TELEMETRIA
# =====================================================
def subir(rows):
    if not rows:
        print("Sin datos para subir en TELEMETRIA.")
        return
    fecha = rows[0][0]
    sh = conectar_sheet()
    ws = sh.worksheet(HOJA_TELEMETRIA)
    if ya_existe_mes(ws, fecha):
        print("Ese mes ya existe en TELEMETRIA. No se sube.")
        return
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Subidas: {len(rows)} filas en TELEMETRIA")

# =====================================================
# SUBIR DATOS UNIDADES
# Detecta automáticamente las columnas por nombre de header
# =====================================================
def subir_datos_unidades(filas):
    if not filas:
        print("Sin datos para subir en DATOS UNIDADES.")
        return

    fecha = filas[0]["fecha"]
    sh = conectar_sheet()
    ws = sh.worksheet(HOJA_DATOS)

    if ya_existe_mes(ws, fecha):
        print("Ese mes ya existe en DATOS UNIDADES. No se sube.")
        return

    # Leer headers de la primera fila para mapear columnas
    headers_raw = ws.row_values(1)
    headers     = [h.strip().lower() for h in headers_raw]
    print("Headers DATOS UNIDADES:", headers_raw)

    def find_col(keywords):
        for kw in keywords:
            for i, h in enumerate(headers):
                if kw in h:
                    return i
        return None

    idx_fecha   = find_col(["fecha"])
    idx_dominio = find_col(["patente", "dominio", "unidad", "placa", "vehiculo"])
    idx_motor   = find_col(["motor", "hora", "hs"])
    idx_ralenti = find_col(["ralenti", "ralentí", "idle"])
    idx_co2     = find_col(["co2", "emisión", "emision", "carbono"])

    print(
        f"Columnas mapeadas → "
        f"fecha:{idx_fecha}  dominio:{idx_dominio}  "
        f"hs_motor:{idx_motor}  ralenti:{idx_ralenti}  co2:{idx_co2}"
    )

    n_cols = max(
        (x for x in [idx_fecha, idx_dominio, idx_motor, idx_ralenti, idx_co2] if x is not None),
        default=9
    ) + 1

    rows_to_append = []
    for fila in filas:
        row = [""] * n_cols
        if idx_fecha   is not None: row[idx_fecha]   = fila["fecha"]
        if idx_dominio is not None: row[idx_dominio] = fila["dominio"]
        if idx_motor   is not None: row[idx_motor]   = fila["hs_motor"]
        if idx_ralenti is not None: row[idx_ralenti] = fila["ralenti"]
        if idx_co2     is not None: row[idx_co2]     = fila["co2"]
        rows_to_append.append(row)

    ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(f"Subidas: {len(rows_to_append)} filas en DATOS UNIDADES")

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":

    # --- TELEMETRIA ---
    filas_telemetria = extraer_tabla()
    subir(filas_telemetria)

    # --- DATOS UNIDADES ---
    filas_unidades = extraer_datos_unidades()
    subir_datos_unidades(filas_unidades)
