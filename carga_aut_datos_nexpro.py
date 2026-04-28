# carga_aut_datos_nexpro.py
# VERSION BOOST COMPLETA
# CARGA:
# 1) TELEMETRIA
# 2) DATOS UNIDADES

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
URL_CONSUMO = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"
URL_PERFORMANCE = "https://nexproconnect.net/Iveco/Reportes/Scoring_UnidadesIveco.aspx"

HOJA_TELEMETRIA = "TELEMETRIA"
HOJA_UNIDADES = "DATOS UNIDADES"


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
    options.add_argument("--window-size=1920,1400")

    return webdriver.Chrome(options=options)


# =====================================================
# LOGIN
# =====================================================

def login(driver):

    driver.get(URL_LOGIN)
    time.sleep(5)

    user = driver.find_element(By.CSS_SELECTOR, "input[type='text']")
    pwd = driver.find_element(By.CSS_SELECTOR, "input[type='password']")

    user.send_keys(NEXPRO_USUARIO)
    pwd.send_keys(NEXPRO_PASSWORD)
    pwd.send_keys(Keys.ENTER)

    time.sleep(8)

    print("LOGIN OK")


# =====================================================
# CLICK TEXTO UNIVERSAL
# =====================================================

def click_texto(driver, texto):

    elems = driver.find_elements(
        By.XPATH,
        "//button | //a | //span | //div"
    )

    for e in elems:
        try:
            if texto.lower() in e.text.lower():
                driver.execute_script("arguments[0].click();", e)
                return True
        except:
            pass

    return False


# =====================================================
# FILTRO HISTORICO CONSUMO
# =====================================================

def filtrar_consumo(driver, desde, hasta):

    driver.get(URL_CONSUMO)
    time.sleep(8)

    click_texto(driver, "Histórico")
    time.sleep(4)

    inputs = driver.find_elements(By.TAG_NAME, "input")

    fechas = []

    for i in inputs:
        try:
            val = i.get_attribute("value")
            if "/" in str(val) and i.is_displayed():
                fechas.append(i)
        except:
            pass

    driver.execute_script("arguments[0].value=arguments[1];", fechas[0], desde)
    driver.execute_script("arguments[0].value=arguments[1];", fechas[1], hasta)

    click_texto(driver, "Visualizar")

    time.sleep(12)


# =====================================================
# EXTRAER TELEMETRIA
# =====================================================

def extraer_telemetria(driver, fecha):

    filas = []

    tablas = driver.find_elements(By.TAG_NAME, "table")

    for tabla in tablas:

        trs = tabla.find_elements(By.TAG_NAME, "tr")

        for tr in trs:

            tds = tr.find_elements(By.TAG_NAME, "td")
            vals = [x.text.strip() for x in tds if x.text.strip()]

            if len(vals) >= 9:

                dominio = vals[0].upper()

                if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

                    litros = num(vals[3])
                    km = num(vals[4])
                    ralenti = num(vals[6])
                    l100 = num(vals[8])

                    filas.append({
                        "fecha": fecha,
                        "dominio": dominio,
                        "km": km,
                        "litros": litros,
                        "ralenti": ralenti,
                        "l100": l100
                    })

    print("TELEMETRIA:", len(filas))
    return filas


# =====================================================
# PERFORMANCE
# =====================================================

def filtrar_performance(driver, desde, hasta):

    driver.get(URL_PERFORMANCE)
    time.sleep(8)

    inputs = driver.find_elements(By.TAG_NAME, "input")

    fechas = []

    for i in inputs:
        try:
            val = i.get_attribute("value")
            if "/" in str(val) and i.is_displayed():
                fechas.append(i)
        except:
            pass

    driver.execute_script("arguments[0].value=arguments[1];", fechas[0], desde)
    driver.execute_script("arguments[0].value=arguments[1];", fechas[1], hasta)

    click_texto(driver, "Buscar")

    time.sleep(12)


def extraer_performance(driver):

    datos = {}

    tablas = driver.find_elements(By.TAG_NAME, "table")

    for tabla in tablas:

        trs = tabla.find_elements(By.TAG_NAME, "tr")

        for tr in trs:

            tds = tr.find_elements(By.TAG_NAME, "td")
            vals = [x.text.strip() for x in tds if x.text.strip()]

            if len(vals) >= 20:

                dominio = vals[2].upper()

                if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

                    hs_motor = vals[14]
                    co2 = num(vals[17])

                    datos[dominio] = {
                        "hs_motor": hs_motor,
                        "co2": co2
                    }

    print("PERFORMANCE:", len(datos))
    return datos


# =====================================================
# GOOGLE SHEETS
# =====================================================

def conectar_sheet():

    creds_dict = json.loads(GOOGLE_CREDS)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
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


def existe_fecha(ws, fecha):

    col = ws.col_values(1)

    for x in col:
        if fecha in str(x):
            return True

    return False


# =====================================================
# SUBIR TELEMETRIA
# =====================================================

def subir_telemetria(sheet, datos):

    ws = sheet.worksheet(HOJA_TELEMETRIA)

    if existe_fecha(ws, datos[0]["fecha"]):
        print("TELEMETRIA YA EXISTE")
        return

    rows = []

    for x in datos:
        rows.append([
            x["fecha"],
            x["dominio"],
            "",
            "",
            x["km"],
            x["litros"],
            x["l100"]
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    print("TELEMETRIA SUBIDA")


# =====================================================
# SUBIR DATOS UNIDADES
# =====================================================

def subir_unidades(sheet, telemetria, perf):

    ws = sheet.worksheet(HOJA_UNIDADES)

    fecha = telemetria[0]["fecha"]

    if existe_fecha(ws, fecha):
        print("DATOS UNIDADES YA EXISTE")
        return

    rows = []

    for x in telemetria:

        dom = x["dominio"]

        hs_motor = ""
        co2 = ""

        if dom in perf:
            hs_motor = perf[dom]["hs_motor"]
            co2 = perf[dom]["co2"]

        rows.append([
            fecha,
            dom,
            hs_motor,
            x["ralenti"],
            co2
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    print("DATOS UNIDADES SUBIDA")


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    desde, hasta, fecha = obtener_mes_anterior()

    print("NEXPRO BOOST")
    print(datetime.now())
    print(desde, hasta)

    driver = crear_driver()

    try:

        login(driver)

        # consumo
        filtrar_consumo(driver, desde, hasta)
        telemetria = extraer_telemetria(driver, fecha)

        # performance
        filtrar_performance(driver, desde, hasta)
        perf = extraer_performance(driver)

    finally:
        driver.quit()

    sh = conectar_sheet()

    subir_telemetria(sh, telemetria)
    subir_unidades(sh, telemetria, perf)

    print("PROCESO FINALIZADO")
