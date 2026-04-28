# carga_aut_datos_nexpro.py
# VERSION DEFINITIVA
# LOGIN + HISTORICO REAL + MES ANTERIOR + GOOGLE SHEETS

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

NEXPRO_USUARIO = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS_JSON"]

_sheet_raw = os.environ["SHEET_ID"]
m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _sheet_raw)
SHEET_ID = m.group(1) if m else _sheet_raw.strip()

URL_LOGIN = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
URL_REPORTE = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"

HOJA_DESTINO = "TELEMETRIA"


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

    primero_actual = hoy.replace(day=1)
    ultimo_anterior = primero_actual - timedelta(days=1)
    primero_anterior = ultimo_anterior.replace(day=1)

    desde = primero_anterior.strftime("%d/%m/%Y")
    hasta = ultimo_anterior.strftime("%d/%m/%Y")
    fecha_carga = ultimo_anterior.strftime("%d/%m/%Y")

    return desde, hasta, fecha_carga


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

    user = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='text']")
        )
    )

    pwd = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='password']")
        )
    )

    driver.execute_script("arguments[0].value='';", user)
    driver.execute_script("arguments[0].value='';", pwd)

    user.send_keys(NEXPRO_USUARIO)
    pwd.send_keys(NEXPRO_PASSWORD)
    pwd.send_keys(Keys.ENTER)

    time.sleep(8)

    print("URL POST LOGIN:", driver.current_url)


# =====================================================
# HISTORICO REAL
# =====================================================

def aplicar_historico(driver, desde, hasta):

    wait = WebDriverWait(driver, 30)

    # Click Histórico
    historico = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[contains(.,'Histórico') or contains(.,'Historico')]"
            )
        )
    )

    driver.execute_script("arguments[0].click();", historico)

    print("Click en Histórico")
    time.sleep(3)

    # Esperar modal y buscar inputs fecha
    cajas = wait.until(
        EC.presence_of_all_elements_located(
            (
                By.XPATH,
                "//input[contains(@value,'/')]"
            )
        )
    )

    if len(cajas) >= 2:

        driver.execute_script("arguments[0].value='';", cajas[0])
        driver.execute_script("arguments[0].value='';", cajas[1])

        cajas[0].send_keys(desde)
        cajas[1].send_keys(hasta)

        print("Fechas cargadas:", desde, hasta)

    time.sleep(1)

    # Click Visualizar modal
    visualizar = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//button[contains(.,'Visualizar')]"
            )
        )
    )

    driver.execute_script("arguments[0].click();", visualizar)

    print("Click en Visualizar")

    time.sleep(12)


# =====================================================
# EXTRAER
# =====================================================

def extraer_tabla():

    desde, hasta, fecha_carga = obtener_mes_anterior()

    print("NEXPRO TELEMETRIA")
    print(datetime.now())
    print("Buscando:", desde, "->", hasta)

    driver = crear_driver()

    filas_finales = []

    try:

        login(driver)

        driver.get(URL_REPORTE)
        time.sleep(8)

        aplicar_historico(driver, desde, hasta)

        # TABLAS
        tablas = driver.find_elements(By.TAG_NAME, "table")

        print("Tablas encontradas:", len(tablas))

        for tabla in tablas:

            filas = tabla.find_elements(By.TAG_NAME, "tr")

            for fila in filas:

                celdas = fila.find_elements(By.TAG_NAME, "td")
                textos = [x.text.strip() for x in celdas if x.text.strip()]

                if len(textos) >= 9:

                    dominio = textos[0].upper().strip()

                    if re.match(
                        r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$",
                        dominio
                    ):

                        litros = num(textos[3])
                        km = num(textos[4])
                        l100 = num(textos[8])

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

    creds = Credentials.from_service_account_file(
        path,
        scopes=scopes
    )

    client = gspread.authorize(creds)

    os.unlink(path)

    return client.open_by_key(SHEET_ID)


def ya_existe_mes(ws, fecha):

    col = ws.col_values(1)

    for x in col:
        if fecha in str(x):
            return True

    return False


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

    filas = extraer_tabla()
    subir(filas)
