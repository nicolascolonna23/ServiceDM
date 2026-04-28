# nexpro_telemetria.py
# VERSION CORREGIDA / MAPEO REAL DE COLUMNAS NEXPRO

import os
import re
import json
import time
import tempfile
import unicodedata
from datetime import datetime, date, timedelta

import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# =====================================================
# VARIABLES
# =====================================================

NEXPRO_USUARIO = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS_JSON"]

_sheet_raw = os.environ["SHEET_ID"]
_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _sheet_raw)
SHEET_ID = _match.group(1) if _match else _sheet_raw.strip()

URL_LOGIN = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
URL_REPORTE = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"

HOJA_DESTINO = "TELEMETRIA"


# =====================================================
# FECHAS
# =====================================================

def obtener_mes_anterior():
    hoy = date.today()

    primero_actual = hoy.replace(day=1)
    ultimo_anterior = primero_actual - timedelta(days=1)
    primero_anterior = ultimo_anterior.replace(day=1)

    desde = primero_anterior.strftime("%d/%m/%Y")
    hasta = ultimo_anterior.strftime("%d/%m/%Y")
    fecha_carga = ultimo_anterior.strftime("%d/%m/%Y")

    return desde, hasta, fecha_carga


# =====================================================
# HELPERS
# =====================================================

def num(txt):
    txt = str(txt).strip()
    txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except:
        return 0.0


def norm(texto):
    return ''.join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )


def click_historico(driver):

    xpaths = [
        "//*[contains(text(),'Histórico')]",
        "//*[contains(text(),'Historico')]"
    ]

    for xp in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xp)
            if elems:
                driver.execute_script("arguments[0].click();", elems[0])
                return True
        except:
            pass

    return False


def click_visualizar(driver):

    botones = driver.find_elements(By.TAG_NAME, "button")

    for b in botones:
        t = norm(b.text)

        if any(x in t for x in ["visualizar", "buscar", "consultar"]):
            driver.execute_script("arguments[0].click();", b)
            return True

    return False


# =====================================================
# CHROME
# =====================================================

def crear_driver():

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,1200")
    options.add_argument("--disable-blink-features=AutomationControlled")

    return webdriver.Chrome(options=options)


# =====================================================
# EXTRAER
# =====================================================

def extraer_tabla():

    desde, hasta, fecha_carga = obtener_mes_anterior()

    print("=" * 50)
    print("Buscando:", desde, "->", hasta)
    print("=" * 50)

    driver = crear_driver()
    wait = WebDriverWait(driver, 30)

    filas_finales = []

    try:

        # LOGIN
        driver.get(URL_LOGIN)
        time.sleep(5)

        user = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='text']")
            )
        )

        driver.execute_script("arguments[0].value=arguments[1];", user, NEXPRO_USUARIO)

        passwd = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            )
        )

        driver.execute_script("arguments[0].value=arguments[1];", passwd, NEXPRO_PASSWORD)

        boton = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='submit'],button[type='submit']")
            )
        )

        driver.execute_script("arguments[0].click();", boton)

        time.sleep(8)

        # REPORTE
        driver.get(URL_REPORTE)
        time.sleep(8)

        click_historico(driver)
        time.sleep(4)

        # FECHAS
        inputs = driver.find_elements(By.TAG_NAME, "input")
        cajas = []

        for i in inputs:
            val = str(i.get_attribute("value") or "")
            if "/" in val and len(val) < 20:
                cajas.append(i)

        if len(cajas) >= 2:

            for caja, valor in [(cajas[0], desde), (cajas[1], hasta)]:

                driver.execute_script(
                    "arguments[0].removeAttribute('readonly');",
                    caja
                )

                driver.execute_script(
                    "arguments[0].value=arguments[1];",
                    caja,
                    valor
                )

                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                    caja
                )

        time.sleep(2)

        click_visualizar(driver)

        time.sleep(15)

        # =================================================
        # TABLA
        # =================================================

        tablas = driver.find_elements(By.TAG_NAME, "table")

        for tabla in tablas:

            filas = tabla.find_elements(By.TAG_NAME, "tr")

            for fila in filas:

                celdas = fila.find_elements(By.TAG_NAME, "td")
                textos = [c.text.strip() for c in celdas]

                # TABLA REAL NEXPRO:
                # 0 Dominio
                # 1 Tipo combustible
                # 2 Unidad
                # 3 Consumo total
                # 4 KM Recorridos
                # 5 Consumo medio
                # 6 Consumo ralenti
                # 7 % ralenti
                # 8 Consumo c/100km

                if len(textos) >= 9:

                    dominio = textos[0].upper().strip()

                    if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

                        litros = num(textos[3])   # consumo total
                        km = num(textos[4])       # km recorridos
                        l100 = num(textos[8])     # consumo c/100km

                        # filtrar filas basura
                        if km <= 0:
                            continue

                        filas_finales.append([
                            fecha_carga,
                            dominio,
                            "",
                            "",
                            km,
                            litros,
                            l100
                        ])

        print("Filas detectadas:", len(filas_finales))

        return filas_finales

    finally:
        driver.quit()


# =====================================================
# GOOGLE SHEETS
# =====================================================

def conectar_sheet():

    creds_dict = json.loads(GOOGLE_CREDS)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False
    ) as f:

        json.dump(creds_dict, f)
        path = f.name

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_file(path, scopes=scopes)

    client = gspread.authorize(creds)

    os.unlink(path)

    return client.open_by_key(SHEET_ID)


# =====================================================
# DUPLICADOS
# =====================================================

def ya_existe_mes(ws, fecha):

    col = ws.col_values(1)

    for x in col:
        if fecha in str(x):
            return True

    return False


# =====================================================
# SUBIR
# =====================================================

def subir(rows):

    if not rows:
        print("Sin datos para subir.")
        return

    fecha = rows[0][0]

    sh = conectar_sheet()
    ws = sh.worksheet(HOJA_DESTINO)

    if ya_existe_mes(ws, fecha):
        print("Ese mes ya existe.")
        return

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    print("Subidas:", len(rows), "filas")


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    print("NEXPRO TELEMETRIA")
    print(datetime.now())

    filas = extraer_tabla()
    subir(filas)
