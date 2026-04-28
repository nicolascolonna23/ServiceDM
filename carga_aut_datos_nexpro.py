# nexpro_telemetria.py
# VERSION DEFINITIVA / DETECCION AUTOMATICA DE COLUMNAS

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
    options.add_argument("--window-size=1800,1400")

    return webdriver.Chrome(options=options)


# =====================================================
# BOTONES
# =====================================================

def click_text(driver, palabras):

    elementos = driver.find_elements(By.XPATH, "//*")

    for e in elementos:
        try:
            texto = norm(e.text)

            if any(p in texto for p in palabras):
                driver.execute_script("arguments[0].click();", e)
                return True
        except:
            pass

    return False


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

            driver.execute_script("arguments[0].value=arguments[1];", cajas[0], desde)
            driver.execute_script("arguments[0].value=arguments[1];", cajas[1], hasta)

        click_text(driver, ["visualizar", "buscar", "consultar"])
        time.sleep(15)

        # =================================================
        # BUSCAR TABLA CORRECTA
        # =================================================

        tablas = driver.find_elements(By.TAG_NAME, "table")

        print("Tablas encontradas:", len(tablas))

        for tabla in tablas:

            filas = tabla.find_elements(By.TAG_NAME, "tr")

            headers = []
            data_start = 0

            for i, fila in enumerate(filas):

                th = fila.find_elements(By.TAG_NAME, "th")

                if th:
                    headers = [norm(x.text) for x in th]
                    data_start = i + 1
                    break

            if not headers:
                continue

            # Detecta columnas automáticamente
            idx_dominio = None
            idx_km = None
            idx_litros = None
            idx_l100 = None

            for i, h in enumerate(headers):

                if "dominio" in h:
                    idx_dominio = i

                elif "km-recorridos" in h or "km recorridos" in h:
                    idx_km = i

                elif "consumo total" in h:
                    idx_litros = i

                elif "100km" in h:
                    idx_l100 = i

            if None in [idx_dominio, idx_km, idx_litros, idx_l100]:
                continue

            print("Tabla válida detectada")

            for fila in filas[data_start:]:

                tds = fila.find_elements(By.TAG_NAME, "td")
                textos = [x.text.strip() for x in tds]

                if len(textos) <= max(idx_dominio, idx_km, idx_litros, idx_l100):
                    continue

                dominio = textos[idx_dominio].upper().strip()

                if not re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):
                    continue

                km = num(textos[idx_km])
                litros = num(textos[idx_litros])
                l100 = num(textos[idx_l100])

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

            break

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
