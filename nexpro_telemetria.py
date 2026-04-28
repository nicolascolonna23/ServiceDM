# nexpro_telemetria.py
# Extrae telemetría mensual (mes anterior) desde Nexpro y la carga en Google Sheets / hoja TELEMETRIA

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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ─────────────────────────────────────────────
# VARIABLES DESDE GITHUB SECRETS
# ─────────────────────────────────────────────
NEXPRO_USUARIO = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS_JSON"]

URL_LOGIN = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
URL_CONSUMO = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"

HOJA_DESTINO = "TELEMETRIA"


# ─────────────────────────────────────────────
# FECHAS MES ANTERIOR
# ─────────────────────────────────────────────
def obtener_mes_anterior():
    hoy = date.today()
    primero_mes_actual = hoy.replace(day=1)
    ultimo_mes_anterior = primero_mes_actual - timedelta(days=1)
    primero_mes_anterior = ultimo_mes_anterior.replace(day=1)

    desde = primero_mes_anterior.strftime("%d/%m/%Y")
    hasta = ultimo_mes_anterior.strftime("%d/%m/%Y")
    fecha_carga = ultimo_mes_anterior.strftime("%d/%m/%Y")

    return desde, hasta, fecha_carga, primero_mes_anterior.month, primero_mes_anterior.year


# ─────────────────────────────────────────────
# NORMALIZAR NUMERO
# ─────────────────────────────────────────────
def num(txt):
    txt = txt.strip().replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except:
        return 0


# ─────────────────────────────────────────────
# LOGIN + EXTRACCION
# ─────────────────────────────────────────────
def extraer_tabla():

    desde, hasta, fecha_carga, mes, anio = obtener_mes_anterior()

    print("Mes a buscar:", desde, "->", hasta)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,1200")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 30)

    resultados = []

    try:
        # LOGIN
        driver.get(URL_LOGIN)
        time.sleep(4)

        user = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text']")))
        password = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))

        user.send_keys(NEXPRO_USUARIO)
        password.send_keys(NEXPRO_PASSWORD)

        boton = driver.find_element(By.CSS_SELECTOR, "input[type='submit'],button[type='submit']")
        boton.click()

        time.sleep(7)

        # IR A MODULO CONSUMO
        driver.get(URL_CONSUMO)
        time.sleep(6)

        # CLICK HISTORICO
        botones = driver.find_elements(By.TAG_NAME, "button")
        for b in botones:
            if "Histórico" in b.text:
                driver.execute_script("arguments[0].click();", b)
                break

        time.sleep(3)

        # INPUTS FECHA
        inputs = driver.find_elements(By.TAG_NAME, "input")

        cajas_fecha = []
        for i in inputs:
            val = i.get_attribute("value")
            if "/" in str(val):
                cajas_fecha.append(i)

        if len(cajas_fecha) >= 2:
            cajas_fecha[0].clear()
            cajas_fecha[0].send_keys(desde)

            cajas_fecha[1].clear()
            cajas_fecha[1].send_keys(hasta)

        time.sleep(1)

        # CLICK VISUALIZAR
        botones = driver.find_elements(By.TAG_NAME, "button")
        for b in botones:
            if "Visualizar" in b.text:
                driver.execute_script("arguments[0].click();", b)
                break

        time.sleep(8)

        # BUSCAR TABLA
        tablas = driver.find_elements(By.TAG_NAME, "table")

        for tabla in tablas:
            filas = tabla.find_elements(By.TAG_NAME, "tr")

            for fila in filas:
                celdas = fila.find_elements(By.TAG_NAME, "td")
                textos = [c.text.strip() for c in celdas]

                if len(textos) >= 8:

                    dominio = textos[0].upper().strip()

                    if re.match(r"^[A-Z]{2}\d{3}[A-Z]{2}$|^[A-Z]{3}\d{3}$", dominio):

                        litros = num(textos[3])
                        km = num(textos[4])
                        l100 = num(textos[7])

                        resultados.append([
                            fecha_carga,     # A
                            dominio,         # B
                            "",              # C
                            "",              # D
                            km,              # E
                            litros,          # F
                            l100             # G
                        ])

        print("Filas encontradas:", len(resultados))

        return resultados

    finally:
        driver.quit()


# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────
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


# ─────────────────────────────────────────────
# EVITAR DUPLICADOS
# ─────────────────────────────────────────────
def ya_existe_mes(ws, fecha_carga):

    datos = ws.col_values(1)

    for x in datos:
        if fecha_carga in str(x):
            return True

    return False


# ─────────────────────────────────────────────
# SUBIR
# ─────────────────────────────────────────────
def subir(rows):

    if not rows:
        print("Sin datos.")
        return

    fecha_carga = rows[0][0]

    sheet = conectar_sheet()
    ws = sheet.worksheet(HOJA_DESTINO)

    if ya_existe_mes(ws, fecha_carga):
        print("Ese mes ya fue cargado.")
        return

    ws.append_rows(rows, value_input_option="USER_ENTERED")

    print("Cargadas", len(rows), "filas")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":

    print("="*50)
    print("NEXPRO TELEMETRIA")
    print(datetime.now())
    print("="*50)

    filas = extraer_tabla()
    subir(filas)
