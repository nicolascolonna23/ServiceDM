# nexpro_telemetria.py
# VERSION CORREGIDA / LECTURA REAL DE COLUMNAS VISIBLES

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


def click_text(driver, palabras):
    botones = driver.find_elements(By.XPATH, "//*")

    for b in botones:
        try:
            t = norm(b.text.strip())
            if any(p in t for p in palabras):
                driver.execute_script("arguments[0].click();", b)
                return True
        except:
            pass

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
    options.add_argument("--window-size=1800,1400")

    return webdriver.Chrome(options=options)


# =====================================================
# EXTRAER
# =====================================================

def extraer_tabla():

    desde, hasta, fecha_carga = obtener_mes_anterior()

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

        driver.execute_script("arguments[0].value=arguments[1];", user, NEXPRO_USUARIO)

        passwd = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='password']")
            )
        )

        driver.execute_script("arguments[0].value=arguments[1];", passwd, NEXPRO_PASSWORD)

        click_text(driver, ["ingresar", "login", "entrar"])
        time.sleep(8)

        # REPORTE
        driver.get(URL_REPORTE)
        time.sleep(8)

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
        time.sleep(15)

        # =================================================
        # TABLA REAL
        # =================================================

        tablas = driver.find_elements(By.TAG_NAME, "table")

        for tabla in tablas:

            filas = tabla.find_elements(By.TAG_NAME, "tr")

            for fila in filas:

                celdas = fila.find_elements(By.TAG_NAME, "td")

                # SOLO celdas visibles con texto
                textos = []

                for c in celdas:
                    try:
                        if c.is_displayed():
                            t = c.text.strip()
                            if t != "":
                                textos.append(t)
                    except:
                        pass

                # LA TABLA VISIBLE REAL TIENE 9 COLUMNAS
                if len(textos) == 9:

                    dominio = textos[0].upper().strip()

                    if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

                        litros = num(textos[3])   # Consumo total
                        km = num(textos[4])       # KM Recorridos
                        l100 = num(textos[8])     # Consumo c/100km

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
# GOOGLE
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

    print("NEXPRO TELEMETRIA")
    print(datetime.now())

    filas = extraer_tabla()
    subir(filas)
