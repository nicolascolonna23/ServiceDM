#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAWK GPS -> kilometraje de cada movil -> Excel + Google Sheets
Login con Selenium (headless), lectura via API JsonMoviles.aspx.
Rapido: ~1 min para toda la flota.

Ademas de los Excel, escribe el km actual (columna H) en las hojas de
services de la planilla SERVICES, buscando la patente en la columna A.

Secrets: HAWK_USER, HAWK_PASS
Opcional (para escribir en la planilla): GOOGLE_CREDENTIALS_JSON, SHEET_ID_SERVICES
"""

import os, re, sys, json, time, datetime, traceback
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

# ---------------------------------------------------------------- sheets cfg
# Planilla SERVICES. Acepta el ID pelado o la URL completa.
_sheet_raw = os.environ.get("SHEET_ID_SERVICES") or os.environ.get("SHEET_ID", "")
_m_sheet   = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", _sheet_raw)
SHEET_ID   = _m_sheet.group(1) if _m_sheet else _sheet_raw.strip()
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# Hojas donde buscar la patente, por gid (Services-LAD/BUE/CAT/COR/LRJ/TUC).
# Se resuelven por gid y no por nombre para que un renombre no rompa el script.
SHEET_GIDS = [
    1845378611,
    1314480090,
    645119605,
    510298228,
    988630097,
    148781788,
]
COL_PATENTE   = 0   # columna A
COL_KM_ACTUAL = 7   # columna H

# SHEETS_DRY_RUN=1 -> muestra que escribiria, sin tocar la planilla
DRY_RUN = os.environ.get("SHEETS_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "si")

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

def norm_pat(texto):
    """'AE 527 FA' y 'AE527FA' son la misma unidad."""
    return re.sub(r"[^A-Z0-9]", "", str(texto or "").upper())

def km_de_celda(texto):
    """Lee el km que ya esta en la planilla (formato es-AR: 1.234.567,89)."""
    t = str(texto or "").strip()
    if not t:
        return 0.0
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^0-9.\-]", "", t) or 0)
    except ValueError:
        return 0.0

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

# ------------------------------------------------------------------ sheets

def conectar_sheets():
    import gspread
    creds = json.loads(GOOGLE_CREDS)
    print(f"  service account : {creds.get('client_email', 'NO ENCONTRADO')}")
    print(f"  SHEET_ID        : {SHEET_ID}")
    gc = gspread.service_account_from_dict(creds)
    return gc.open_by_key(SHEET_ID)

def actualizar_sheets(df):
    """
    Escribe el km actual en la columna H de cada hoja de SHEET_GIDS,
    buscando la patente en la columna A.

    Solo pisa el valor si el km de Hawk es mayor al que ya esta cargado:
    el odometro nunca baja, asi que un valor menor es una lectura mala y
    no debe borrar el bueno.
    """
    if not GOOGLE_CREDS or not SHEET_ID:
        print("\n[sheets] sin GOOGLE_CREDENTIALS_JSON o SHEET_ID_SERVICES: no se actualiza la planilla")
        return

    import gspread

    # patente normalizada -> km leido de Hawk
    kms = {}
    for _, f in df.iterrows():
        p = norm_pat(f["Patente"])
        if p and pd.notna(f["Kilometraje"]):
            kms[p] = float(f["Kilometraje"])
    if not kms:
        print("\n[sheets] sin kilometrajes para escribir")
        return

    print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] Google Sheets"
          f"{' (DRY RUN)' if DRY_RUN else ''}...")

    sheet = None
    for intento in range(1, 4):
        try:
            sheet = conectar_sheets()
            break
        except Exception as e:
            print(f"  intento {intento}/3 fallo: {type(e).__name__}: {str(e)[:200]}")
            if intento == 3:
                print("  no se pudo conectar: los Excel igual quedaron guardados")
                return
            time.sleep(10)

    total, sin_dato = 0, []

    for gid in SHEET_GIDS:
        try:
            ws = sheet.get_worksheet_by_id(gid)
        except Exception:
            print(f"  hoja gid={gid} no encontrada")
            continue

        try:
            datos = ws.get_all_values()
        except Exception as e:
            print(f"  {ws.title}: no se pudo leer ({type(e).__name__}: {str(e)[:120]})")
            continue

        batch, saltadas = [], 0
        for idx, fila in enumerate(datos, start=1):
            if len(fila) <= COL_PATENTE:
                continue
            pat = norm_pat(fila[COL_PATENTE])
            # descarta encabezados y celdas que no son patente
            if not re.match(r"^([A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$", pat):
                continue
            if pat not in kms:
                sin_dato.append(f"{ws.title}: {fila[COL_PATENTE].strip()}")
                continue

            km_nuevo   = kms[pat]
            km_actual  = km_de_celda(fila[COL_KM_ACTUAL] if len(fila) > COL_KM_ACTUAL else "")
            if km_nuevo <= km_actual:
                saltadas += 1
                continue

            batch.append({
                "range":  gspread.utils.rowcol_to_a1(idx, COL_KM_ACTUAL + 1),
                "values": [[round(km_nuevo, 2)]],
            })
            total += 1

        if batch and not DRY_RUN:
            ws.batch_update(batch, value_input_option="USER_ENTERED")

        print(f"  {ws.title}: {len(batch)} actualizadas, {saltadas} sin cambio")

    print(f"\n[sheets] {'se escribirian' if DRY_RUN else 'actualizadas'}: {total} unidades")
    if sin_dato:
        print(f"[sheets] sin lectura de Hawk ({len(sin_dato)}):")
        for p in sin_dato[:15]:
            print(f"   - {p}")
        if len(sin_dato) > 15:
            print(f"   ... y {len(sin_dato) - 15} mas")

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
        # dd se reinicia en cada vuelta: si una lectura falla no puede
        # quedar el detalle del movil anterior y asignar mal la patente
        dd = None
        km = ult = err = None
        try:
            dd = obtener_movil(s, m["idGPS"])
            if dd is None:
                err = "sin datos (500)"
            else:
                km = dd.get("Kilometraje")
                ult = parse_fecha(dd.get("FechaServer"))
        except Exception as e:
            err = str(e)[:100]
        pat = (m.get("Patente") or m.get("Descripcion") or "").strip()
        if dd:
            pat = (dd.get("Descripcion") or dd.get("Patente") or pat).strip()
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

    # Los Excel ya estan escritos: un fallo de la planilla no debe tirar el job
    try:
        actualizar_sheets(df)
    except Exception:
        print("\n[sheets] error al actualizar la planilla:")
        traceback.print_exc()

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
