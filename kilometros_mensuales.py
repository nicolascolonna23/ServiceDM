import os
import json
import time
import smtplib
from calendar import monthrange
from datetime import date, timedelta
from email.message import EmailMessage

import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL_LOGIN      = "https://cloud.dazsistemas.com.ar/menu/Login.aspx"
URL_CONSUMOS   = "https://cloud.dazsistemas.com.ar/stock/Consumos_Mensuales.aspx"
SPREADSHEET_ID = "1u7cckay0IJ60bfoKk2OZo-TjCvTbH9O1wKxNFdSKDCQ"
HOJA_GID       = 1044040871   # pestaña destino (gid del link)

USUARIO            = os.environ["CUBIERTAS_USUARIO"]
PASSWORD           = os.environ["CUBIERTAS_PASSWORD"]
GOOGLE_CREDS_RAW   = os.environ["GOOGLE_CREDENTIALS"]
GMAIL_USER         = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_DEST         = os.environ["GMAIL_DEST"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def crear_driver():
    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1400")
    return webdriver.Chrome(options=opciones)


def login(driver):
    driver.get(URL_LOGIN)
    wait = WebDriverWait(driver, 20)
    campo_usuario = wait.until(EC.presence_of_element_located((By.ID, "txtUsuario")))
    driver.find_element(By.ID, "txtPass").send_keys(PASSWORD)
    campo_usuario.send_keys(USUARIO)
    driver.find_element(By.ID, "btnLogin").click()
    wait.until(EC.url_contains("/menu/"))


def _entrar_stock(driver):
    wait = WebDriverWait(driver, 20)
    wait.until(lambda d: d.execute_script("return typeof openLink === 'function';"))
    ventanas_antes = set(driver.window_handles)
    driver.execute_script("openLink('W255TK', '853', '/stock/Login.aspx?empresa=853');")
    time.sleep(3)
    nuevas = set(driver.window_handles) - ventanas_antes
    if nuevas:
        driver.switch_to.window(nuevas.pop())
    wait.until(EC.url_contains("/stock/"))
    time.sleep(2)


def mes_anterior():
    hoy = date.today()
    ultimo_mes = hoy.replace(day=1) - timedelta(days=1)
    return ultimo_mes.month, ultimo_mes.year


def fecha_ultimo_dia(mes, anio):
    # Formato del sheet destino: DD/MM/AAAA con el último día del mes.
    dia = monthrange(anio, mes)[1]
    return f"{dia:02d}/{mes:02d}/{anio}"


def buscar_consumos(driver, mes, anio):
    driver.get(URL_CONSUMOS)
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.XPATH, "//input")))
    time.sleep(1)

    campo_mes = driver.find_element(By.XPATH,
        "//input[contains(@id,'Mes') or contains(@name,'Mes') or "
        "contains(@id,'mes') or contains(@name,'mes')]")
    campo_anio = driver.find_element(By.XPATH,
        "//input[contains(@id,'Anio') or contains(@name,'Anio') or "
        "contains(@id,'Year') or contains(@name,'Year') or "
        "contains(@id,'anio') or contains(@id,'Año')]")

    campo_mes.clear()
    campo_mes.send_keys(str(mes).zfill(2))
    campo_anio.clear()
    campo_anio.send_keys(str(anio))

    driver.find_element(By.XPATH,
        "//a[contains(normalize-space(),'Buscar')] | "
        "//input[@value='Buscar'] | //button[contains(normalize-space(),'Buscar')]"
    ).click()

    wait.until(EC.url_contains("Reportes"))
    time.sleep(4)


def _parsear_numero(txt):
    # Formato argentino: 1.234,56 → 1234.56
    limpio = txt.replace("$", "").replace(" ", "").strip()
    # Quitar paréntesis de negativos: (500) → -500
    negativo = limpio.startswith("(") and limpio.endswith(")")
    if negativo:
        limpio = limpio[1:-1]
    limpio = limpio.replace(".", "").replace(",", ".")
    try:
        valor = float(limpio)
        return -valor if negativo else valor
    except ValueError:
        return None


def _extraer_tabla_actual(driver):
    """
    La tabla de datos tiene exactamente 13 columnas por fila:
    [vacío, Unidad, Interno, Patente, Año, Período, Km, Litros, Importe, Lub, Cub, Rep, Total]
    Las filas de subtotal tienen Patente vacía → se descartan.
    Devuelve (patente, km) por fila, sólo cuando km > 0.
    """
    driver.switch_to.default_content()
    resultado = []

    for tabla in driver.find_elements(By.TAG_NAME, "table"):
        datos_tabla = []
        for fila in tabla.find_elements(By.TAG_NAME, "tr"):
            celdas = fila.find_elements(By.TAG_NAME, "td")
            if len(celdas) != 13:
                continue
            patente = celdas[3].text.strip()
            km_txt = celdas[6].text.strip()
            # Descartar filas de encabezado o subtotal (patente vacía o es texto de header)
            if not patente or patente == "Patente" or "/" in patente:
                continue
            km = _parsear_numero(km_txt)
            if km is None or km == 0:
                continue
            datos_tabla.append((patente, km))

        if datos_tabla:
            resultado.extend(datos_tabla)
            break  # La primera tabla con datos válidos es la correcta

    print(f"  Filas extraídas esta página: {len(resultado)}")
    return resultado


def extraer_todos_los_datos(driver):
    todos = []
    while True:
        filas = _extraer_tabla_actual(driver)
        todos.extend(filas)

        try:
            siguiente = driver.find_element(By.XPATH,
                "//input[@title='Next Page' or @title='Página siguiente' or @title='next page'] | "
                "//a[@title='Next Page' or @title='Página siguiente']")
            if not siguiente.is_enabled():
                break
            siguiente.click()
            time.sleep(3)
        except Exception:
            break

    return todos


def escribir_en_hoja(filas, mes, anio):
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS_RAW), scopes=SCOPES)
    libro = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    hoja = libro.get_worksheet_by_id(HOJA_GID)

    fecha_str = fecha_ultimo_dia(mes, anio)
    if fecha_str in hoja.col_values(1):
        print(f"Fecha {fecha_str} ya existe, se omite.")
        return

    # Columnas: A=fecha, B=dominio, C-E vacías, F=km
    nuevas = [[fecha_str, patente, "", "", "", km] for patente, km in filas]
    hoja.append_rows(nuevas, value_input_option="USER_ENTERED")
    print(f"Agregadas {len(nuevas)} filas para {fecha_str}.")


def enviar_error(error_txt):
    msg = EmailMessage()
    msg["Subject"] = "ERROR - Kilómetros mensuales DAZ"
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_DEST
    msg.set_content(
        "Ocurrió un error al procesar los kilómetros mensuales.\n\n"
        f"Detalle:\n{error_txt}"
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


if __name__ == "__main__":
    driver = crear_driver()
    try:
        mes, anio = mes_anterior()
        print(f"Procesando período: {mes:02d}/{anio}")
        login(driver)
        _entrar_stock(driver)
        buscar_consumos(driver, mes, anio)
        filas = extraer_todos_los_datos(driver)
        print(f"Total filas extraídas: {len(filas)}")
        if not filas:
            raise RuntimeError("No se encontraron datos en el reporte.")
        escribir_en_hoja(filas, mes, anio)
    except Exception as e:
        try:
            enviar_error(str(e))
        except Exception:
            pass
        raise
    finally:
        driver.quit()
