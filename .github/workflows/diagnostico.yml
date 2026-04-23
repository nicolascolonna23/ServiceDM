"""
diagnostico_menu.py
Hace login en Nexpro y guarda el HTML del home + lista todos los links del menú.
Corré esto UNA VEZ para descubrir las URLs correctas de los reportes.
"""

import os
import re
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

NEXPRO_URL      = "https://nexproconnect.net/Iveco/Login/Login2.aspx"
NEXPRO_USUARIO  = os.environ["NEXPRO_USUARIO"]
NEXPRO_PASSWORD = os.environ["NEXPRO_PASSWORD"]

opciones = Options()
opciones.add_argument("--headless=new")
opciones.add_argument("--no-sandbox")
opciones.add_argument("--disable-dev-shm-usage")
opciones.add_argument("--disable-gpu")
opciones.add_argument("--window-size=1280,900")
opciones.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")

driver = webdriver.Chrome(options=opciones)
wait   = WebDriverWait(driver, 25)

try:
    # --- Login ---
    driver.get(NEXPRO_URL)
    time.sleep(4)

    campo_usuario = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text']")))
    campo_pass    = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))

    driver.execute_script("arguments[0].value = arguments[1];", campo_usuario, NEXPRO_USUARIO)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_usuario)
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_usuario)

    driver.execute_script("arguments[0].value = arguments[1];", campo_pass, NEXPRO_PASSWORD)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", campo_pass)
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", campo_pass)
    time.sleep(0.5)

    btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit']")
    driver.execute_script("arguments[0].click();", btn)
    time.sleep(8)

    print(f"✅ Login OK — URL: {driver.current_url}")
    print(f"   Título: {driver.title}\n")

    # --- Guardar HTML del home ---
    with open("home.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print("📄 HTML del home guardado: home.html\n")

    # --- Listar TODOS los links ---
    links = driver.find_elements(By.TAG_NAME, "a")
    print(f"🔗 LINKS ENCONTRADOS ({len(links)}):")
    print("-" * 60)
    urls_unicas = set()
    for link in links:
        href = link.get_attribute("href") or ""
        texto = link.text.strip()
        if href and "nexproconnect" in href and href not in urls_unicas:
            urls_unicas.add(href)
            print(f"  [{texto or '(sin texto)'}]  →  {href}")

    # --- Buscar elementos del menú (nav, sidebar) ---
    print("\n\n🗂️  ELEMENTOS DE MENÚ (nav/li/sidebar):")
    print("-" * 60)
    menu_items = driver.find_elements(By.CSS_SELECTOR, "nav a, .menu a, .sidebar a, li a, .nav a")
    for item in menu_items:
        href  = item.get_attribute("href") or ""
        texto = item.text.strip()
        if texto and href:
            print(f"  {texto}  →  {href}")

    # --- Buscar iframes (Nexpro a veces carga reportes en iframe) ---
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    if iframes:
        print(f"\n\n🖼️  IFRAMES ENCONTRADOS ({len(iframes)}):")
        print("-" * 60)
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            iid = iframe.get_attribute("id") or ""
            print(f"  id={iid}  src={src}")

finally:
    driver.quit()
    print("\n✅ Diagnóstico completo. Revisá home.html y el log de arriba.")
