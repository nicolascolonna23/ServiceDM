# nexpro_telemetria.py
# VERSION DEFINITIVA / TABLA AJAX + IFRAME + HTML DINAMICO

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
# HELPERS
# =====================================================

def norm(txt):
    txt = str(txt).lower().strip()
    return ''.join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def num(txt):
    txt = str(txt).strip().replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except:
        return 0


def obtener_mes_anterior():
    hoy = date.today()

    primero_actual = hoy.replace(day=1)
    ultimo_anterior = primero_actual - timedelta(days=1)
    primero_anterior = ultimo_anterior.replace(day=1)

    return (
        primero_anterior.strftime("%d/%m/%Y"),
        ultimo_anterior.strftime("%d/%m/%Y"),
        ultimo_anterior.strftime("%d/%m/%Y")
    )


# =====================================================
# CHROME
# =====================================================

def crear_driver():

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,2200")

    return webdriver.Chrome(options=options)


# =====================================================
# CLICK POR TEXTO
# =====================================================

def click_text(driver, palabras):

    elems = driver.find_elements(By.XPATH, "//*")

    for e in elems:
        try:
            texto = norm(e.text)

            if any(p in texto for p in palabras):
                driver.execute_script("arguments[0].click();", e)
                return True
        except:
            pass

    return False


# =====================================================
# BUSCAR TABLAS EN TODO EL DOM
# =====================================================

def obtener_tablas(driver):

    tablas = driver.find_elements(By.TAG_NAME, "table")

    if tablas:
        return tablas

    # buscar en iframes
    iframes = driver.find_elements(By.TAG_NAME, "iframe")

    for i in range(len(iframes)):
        try:
            driver.switch_to.default_content()
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            driver.switch_to.frame(frames[i])

            tablas = driver.find_elements(By.TAG_NAME, "table")

            if tablas:
                print("Tabla encontrada dentro de iframe:", i)
                return tablas

        except:
            pass

    driver.switch_to.default_content()
    return []


# =====================================================
# EXTRAER
# =====================================================

def extraer_tabla():

    desde, hasta, fecha_carga = obtener_mes_anterior()

    print("NEXPRO TELEMETRIA")
    print(datetime.now())
    print("Buscando:", desde, "->", hasta)

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

        passwd = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            )
        )

        driver.execute_script("arguments[0].value=arguments[1];", user, NEXPRO_USUARIO)
        driver.execute_script("arguments[0].value=arguments[1];", passwd, NEXPRO_PASSWORD)

        click_text(driver, ["ingresar", "login", "entrar"])

        time.sleep(8)

        # REPORTE
        driver.get(URL_REPORTE)
        time.sleep(10)

        click_text(driver, ["historico"])
        time.sleep(4)

        # FECHAS
        inputs = driver.find_elements(By.TAG_NAME, "input")

        cajas = []

        for i in inputs:
            val = str(i.get_attribute("value") or "")
            if "/" in val:
                cajas.append(i)

        if len(cajas) >= 2:

            driver.execute_script(
                "arguments[0].value=arguments[1];",
                cajas[0],
                desde
            )

            driver.execute_script(
                "arguments[0].value=arguments[1];",
                cajas[1],
                hasta
            )

        click_text(driver, ["visualizar", "buscar", "consultar"])

        print("Esperando carga AJAX...")
        time.sleep(20)

        # =================================================
        # TABLAS
        # =================================================

        tablas = obtener_tablas(driver)

        print("Tablas encontradas:", len(tablas))

        for tabla in tablas:

            filas = tabla.find_elements(By.TAG_NAME, "tr")

            for fila in filas:

                tds = fila.find_elements(By.TAG_NAME, "td")
                textos = [x.text.strip() for x in tds if x.text.strip() != ""]

                if len(textos) >= 9:

                    dominio = textos[0].upper().strip()

                    if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

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

    creds = Credentials.from_service_account_file(path, scopes=scopes)

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
