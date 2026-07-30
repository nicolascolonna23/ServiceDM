#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAWK GPS -> kilometraje de cada movil -> Excel
Login con Selenium (headless), lectura via API JsonMoviles.aspx.
Rapido: ~1 min para toda la flota.

Secrets: HAWK_USER, HAWK_PASS
"""

import os, re, sys, time, datetime, traceback
import requests
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE  = "http://www.hawkgps.com.ar/HawkEyeWeb/"
URL   = BASE + "Index.aspx"
USER  = os.environ.get("HAWK_USER", "")
PASS  = os.environ.get("HAWK_PASS", "")
# solo estas empresas (subcadena, mayus). vacio = todas
SOLO  = [s.strip().upper() for s in os.environ.get("SOLO_EMPRESAS", "CATAMARCA").split(",") if s.strip()]

OUT_DIR = "data"

JS_PATENTES = """
const re = /\\b([A-Z]{2}\\d{3}[A-Z]{2}|[A-Z]{3}\\d{3})\\b/;
const out = [], vistos = new Set();
document.querySelectorAll('div,li,td,span,a').forEach(el => {
  if (el.children.length > 2) return;
  const t = (el.innerText || '').trim();
  if (!t || t.length > 60) return;
  const m = t.match(re);
  if (!m || vistos.has(m[1])) return;
  vistos.add(m[1]); out.push(m[1]);
});
return out;
"""

# ------------------------------------------------------------------ util

def crear_driver():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1600,1000")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--ignore-certificate-errors")
    o.add_argument("--allow-running-insecure-content")
    return webdriver.Chrome(options=o)

def shot(d, nombre):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        d.save_screenshot(f"{OUT_DIR}/{nombre}.png")
    except Exception:
        pass

def parse_fecha(s):
    m = re.search(r"/Date\((\d+)\)/", s or "")
    if not m:
        return ""
    ts = int(m.group(1)) / 1000
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=-3)))
    return dt.strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------------------------------------------ login

def login(d):
    d.get(URL)
    time.sleep(4)
    fin, pw = time.time() + 45, None
    while time.time() < fin:
        v = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type='password']") if e.is_displayed()]
        if v:
            pw = v[0]; break
        if d.execute_script(JS_PATENTES):
            return   # ya habia sesion
        time.sleep(1)
    if pw is None:
        shot(d, "err_login"); raise SystemExit("No encuentro campo contrasena.")

    txts = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type='text'],input:not([type])")
            if e.is_displayed()]
    if not txts:
        shot(d, "err_login"); raise SystemExit("No encuentro campo usuario.")
    txts[0].clear(); txts[0].send_keys(USER)
    pw.clear(); pw.send_keys(PASS)

    ok = False
    for sel in ["input[type='submit']", "button[type='submit']", "button", "a[id*='ogin']"]:
        for b in d.find_elements(By.CSS_SELECTOR, sel):
            etq = (b.text or "") + " " + (b.get_attribute("value") or "")
            if b.is_displayed() and re.search(r"ingres|entrar|login|acceder|aceptar", etq, re.I):
                b.click(); ok = True; break
        if ok:
            break
    if not ok:
        pw.send_keys(Keys.ENTER)

    fin = time.time() + 60
    while time.time() < fin:
        time.sleep(2)
        if d.execute_script(JS_PATENTES):
            print("login OK")
            return
    shot(d, "err_post_login"); raise SystemExit("Login sin lista de moviles.")

# ------------------------------------------------------------------ api

def hacer_sesion(d):
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": d.execute_script("return navigator.userAgent"),
        "Referer": URL,
        "Origin": "http://www.hawkgps.com.ar",
    })
    for c in d.get_cookies():
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s

def obtener_todos(s):
    r = s.post(BASE + "JsonMoviles.aspx/ObtenerTodos",
               json={"idEmpresa": 0, "ListaID": 0, "idTipoMovil": 0}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("d", data) if isinstance(data, dict) else data

def obtener_movil(s, id_gps):
    r = s.post(BASE + "JsonMoviles.aspx/ObtenerMovil",
               json={"idGPS": id_gps}, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("d")

# ------------------------------------------------------------------ main

def main():
    if not USER or not PASS:
        raise SystemExit("Faltan secrets HAWK_USER / HAWK_PASS")
    os.makedirs(OUT_DIR, exist_ok=True)

    d = crear_driver()
    try:
        login(d)
        s = hacer_sesion(d)
    finally:
        try:
            d.quit()
        except Exception:
            pass

    lista = obtener_todos(s)
    if not isinstance(lista, list):
        raise SystemExit(f"ObtenerTodos devolvio inesperado: {str(lista)[:200]}")
    print(f"flota total: {len(lista)}")

    if SOLO:
        lista = [m for m in lista
                 if any(x in (str(m.get("NombreEmpresa", "")) + str(m.get("NombreFlota", ""))).upper()
                        for x in SOLO)]
    print(f"a procesar: {len(lista)}  (filtro={SOLO or 'ninguno'})")

    ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))
    ahora_txt = ahora.strftime("%Y-%m-%d %H:%M:%S")

    filas = []
    for i, m in enumerate(lista, 1):
        km = ult = err = None
        try:
            dd = obtener_movil(s, m["idGPS"])
            if dd is None:
                err = "sin datos (500)"
            else:
                km = dd.get("Kilometraje")
                ult = parse_fecha(dd.get("FechaServer"))
                pat = (dd.get("Descripcion") or dd.get("Patente") or "").strip()
        except Exception as e:
            err = str(e)[:100]
        pat = (m.get("Patente") or m.get("Descripcion") or "").strip()
        try:
            if dd:
                pat = (dd.get("Descripcion") or dd.get("Patente") or pat).strip()
        except Exception:
            pass
        filas.append({
            "Patente": pat,
            "Kilometraje": round(km, 2) if isinstance(km, (int, float)) else None,
            "Ultimo_reporte": ult,
            "idGPS": m.get("idGPS"),
            "Fecha_lectura": ahora_txt,
            "Error": err,
        })
        if i % 20 == 0:
            print(f"  {i}/{len(lista)}")

    df = pd.DataFrame(filas)
    df["Patente"] = df["Patente"].replace({"AF527AE": "AE527FA", "AE527AE": "AE527FA"})
    df = df[df["Kilometraje"].notna()].sort_values("Patente").reset_index(drop=True)
    stamp = ahora.strftime("%Y%m%d_%H%M")
    df.to_excel(f"{OUT_DIR}/kilometrajes_{stamp}.xlsx", index=False)
    df.to_excel(f"{OUT_DIR}/kilometrajes_latest.xlsx", index=False)
    hist = f"{OUT_DIR}/historico.csv"
    df.to_csv(hist, mode="a", header=not os.path.exists(hist), index=False)

    ok = int(df["Kilometraje"].notna().sum())
    print(f"\nOK {ok}/{len(df)} -> {OUT_DIR}/kilometrajes_{stamp}.xlsx")
    if ok == 0:
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
