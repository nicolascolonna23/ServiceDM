# carga_aut_datos_nexpro.py
# SCRIPT DIAGNOSTICO DEFINITIVO - GITHUB ACTIONS
# OBJETIVO:
# 1) Login
# 2) Ir reporte
# 3) Click botones
# 4) Guardar screenshot
# 5) Guardar HTML
# 6) Mostrar que existe realmente en DOM

import os
import re
import time
from datetime import datetime

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

URL_LOGIN = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
URL_REPORTE = "https://nexproconnect.net/Iveco/ConsumoIveco/ConsumoIveco.aspx"


# =====================================================
# CHROME
# =====================================================

def crear_driver():

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,3000")

    driver = webdriver.Chrome(options=options)

    return driver


# =====================================================
# HELPERS
# =====================================================

def click_por_texto(driver, palabras):

    elems = driver.find_elements(By.XPATH, "//*")

    for e in elems:
        try:
            txt = e.text.strip().lower()

            if any(p in txt for p in palabras):
                driver.execute_script("arguments[0].click();", e)
                print("CLICK:", txt[:80])
                return True

        except:
            pass

    return False


def guardar_archivos(driver, nombre):

    png = f"{nombre}.png"
    html = f"{nombre}.html"

    driver.save_screenshot(png)

    with open(html, "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    print("Guardado:", png)
    print("Guardado:", html)


def resumen_dom(driver):

    tablas = driver.find_elements(By.TAG_NAME, "table")
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    divs = driver.find_elements(By.TAG_NAME, "div")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    trs = driver.find_elements(By.TAG_NAME, "tr")

    print("=" * 50)
    print("RESUMEN DOM")
    print("Tables :", len(tablas))
    print("Iframes:", len(iframes))
    print("Divs   :", len(divs))
    print("Buttons:", len(buttons))
    print("TR rows:", len(trs))
    print("=" * 50)

    print("BOTONES VISIBLES:")
    for b in buttons[:20]:
        try:
            t = b.text.strip()
            if t:
                print("-", t)
        except:
            pass


# =====================================================
# MAIN
# =====================================================

driver = crear_driver()
wait = WebDriverWait(driver, 30)

try:

    print("NEXPRO DIAGNOSTICO")
    print(datetime.now())

    # =============================================
    # LOGIN
    # =============================================

    driver.get(URL_LOGIN)
    time.sleep(5)

    guardar_archivos(driver, "01_login")

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

    driver.execute_script(
        "arguments[0].value=arguments[1];",
        user,
        NEXPRO_USUARIO
    )

    driver.execute_script(
        "arguments[0].value=arguments[1];",
        pwd,
        NEXPRO_PASSWORD
    )

    click_por_texto(driver, ["ingresar", "login", "entrar"])

    time.sleep(8)

    print("URL POST LOGIN:", driver.current_url)

    guardar_archivos(driver, "02_post_login")

    # =============================================
    # REPORTE
    # =============================================

    driver.get(URL_REPORTE)
    time.sleep(10)

    print("URL REPORTE:", driver.current_url)

    guardar_archivos(driver, "03_reporte_inicial")
    resumen_dom(driver)

    # =============================================
    # HISTORICO
    # =============================================

    click_por_texto(driver, ["historico"])
    time.sleep(5)

    guardar_archivos(driver, "04_post_historico")
    resumen_dom(driver)

    # =============================================
    # VISUALIZAR
    # =============================================

    click_por_texto(driver, ["visualizar", "buscar", "consultar"])
    time.sleep(15)

    guardar_archivos(driver, "05_post_visualizar")
    resumen_dom(driver)

    # =============================================
    # IFRAMES
    # =============================================

    iframes = driver.find_elements(By.TAG_NAME, "iframe")

    print("Analizando iframes:", len(iframes))

    for i in range(len(iframes)):

        try:
            driver.switch_to.default_content()

            frames = driver.find_elements(By.TAG_NAME, "iframe")
            driver.switch_to.frame(frames[i])

            print("Dentro iframe", i)

            guardar_archivos(driver, f"iframe_{i}")

            tablas = driver.find_elements(By.TAG_NAME, "table")
            print("Tables iframe:", len(tablas))

        except Exception as e:
            print("Iframe error:", i, str(e))

    driver.switch_to.default_content()

    print("DIAGNOSTICO FINALIZADO")

finally:
    driver.quit()
