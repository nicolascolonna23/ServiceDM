#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAWK GPS -> kilometraje de cada movil -> Excel
Corre headless en GitHub Actions.

Secrets requeridos: HAWK_USER, HAWK_PASS
"""

import os, re, sys, time, datetime, traceback
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

URL          = "http://www.hawkgps.com.ar/HawkEyeWeb/Index.aspx"
USER         = os.environ.get("HAWK_USER", "")
PASS         = os.environ.get("HAWK_PASS", "")
MAX_MOVILES  = int(os.environ.get("MAX_MOVILES", "0"))   # 0 = todos
HEADLESS     = os.environ.get("HEADLESS", "1") == "1"

OUT_DIR      = "data"
ESPERA_MODAL = 12
PAUSA        = 0.7

# ------------------------------------------------------------------ JS

JS_FILAS = """
const re = /\\b([A-Z]{2}\\d{3}[A-Z]{2}|[A-Z]{3}\\d{3})\\b/;
const out = [];
const vistos = new Set();
document.querySelectorAll('div,li,td,span,a').forEach(el => {
  if (el.children.length > 2) return;
  const t = (el.innerText || '').trim();
  if (!t || t.length > 60) return;
  const m = t.match(re);
  if (!m) return;
  if (vistos.has(m[1])) return;
  let row = el;
  for (let i = 0; i < 5 && row.parentElement; i++) {
    if (row.offsetHeight >= 35 && row.offsetHeight <= 90 && row.offsetWidth > 200) break;
    row = row.parentElement;
  }
  vistos.add(m[1]);
  row.setAttribute('data-km', m[1]);
  out.push(m[1]);
});
return out;
"""

JS_FLECHA = """
const row = arguments[0];
const h = [...row.querySelectorAll('div,span,img,a,td')]
  .filter(e => e.offsetParent !== null && e.offsetWidth > 8 && e.offsetWidth < 60);
return h.length ? h[h.length-1] : row;
"""

JS_CLICK = """
const el = arguments[0];
el.scrollIntoView({block:'center'});
['mouseover','mousedown','mouseup','click'].forEach(n =>
  el.dispatchEvent(new MouseEvent(n, {bubbles:true, cancelable:true, view:window})));
"""

JS_ITEM = """
const txts = arguments[0];
const els = [...document.querySelectorAll('div,li,a,span,td')]
  .filter(e => e.children.length === 0 && e.offsetParent !== null &&
               txts.includes((e.innerText||'').trim()));
if (!els.length) return false;
const el = els[els.length-1];
['mouseover','mousedown','mouseup','click'].forEach(n =>
  el.dispatchEvent(new MouseEvent(n, {bubbles:true, cancelable:true, view:window})));
return true;
"""

JS_CAMPO = """
const etiquetas = arguments[0];
for (const etq of etiquetas) {
  const els = [...document.querySelectorAll('div,td,span,label,li')]
    .filter(e => e.offsetParent !== null && (e.innerText||'').trim().startsWith(etq));
  for (const e of els) {
    const t = (e.parentElement ? e.parentElement.innerText : e.innerText) || '';
    const i = t.indexOf(etq);
    if (i < 0) continue;
    const resto = t.slice(i + etq.length).split('\\n')[0].replace(':','').trim();
    if (resto) return resto;
  }
}
return null;
"""

JS_CERRAR = """
const c = [...document.querySelectorAll('div,span,a,img,button')]
  .filter(e => e.offsetParent !== null &&
    ((e.innerText||'').trim() === '\\u00d7' || (e.innerText||'').trim() === 'X' ||
     /close|cerrar/i.test(e.className + ' ' + (e.title||'') + ' ' + (e.id||''))));
if (!c.length) return false;
const el = c[c.length-1];
['mousedown','mouseup','click'].forEach(n =>
  el.dispatchEvent(new MouseEvent(n, {bubbles:true, cancelable:true, view:window})));
return true;
"""

# ------------------------------------------------------------------ driver

def crear_driver():
    o = Options()
    if HEADLESS:
        o.add_argument("--headless=new")
    o.add_argument("--window-size=1920,1080")
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
        with open(f"{OUT_DIR}/{nombre}.html", "w") as f:
            f.write(d.page_source)
    except Exception:
        pass

# ------------------------------------------------------------------ login

def login(d):
    d.get(URL)
    time.sleep(4)

    fin = time.time() + 45
    pw = None
    while time.time() < fin:
        cands = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type='password']")
                 if e.is_displayed()]
        if cands:
            pw = cands[0]
            break
        if d.execute_script(JS_FILAS):          # sesion ya activa
            return
        time.sleep(1)

    if pw is None:
        shot(d, "err_login")
        raise SystemExit("No encuentro el campo de contrasena.")

    txts = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type='text'],input:not([type])")
            if e.is_displayed()]
    if not txts:
        shot(d, "err_login")
        raise SystemExit("No encuentro el campo de usuario.")

    txts[0].clear(); txts[0].send_keys(USER)
    pw.clear();      pw.send_keys(PASS)

    ok = False
    for sel in ["input[type='submit']", "button[type='submit']", "button", "a[id*='ogin']"]:
        for b in d.find_elements(By.CSS_SELECTOR, sel):
            etiqueta = (b.text or "") + " " + (b.get_attribute("value") or "")
            if b.is_displayed() and re.search(r"ingres|entrar|login|acceder|aceptar", etiqueta, re.I):
                d.execute_script(JS_CLICK, b); ok = True; break
        if ok:
            break
    if not ok:
        pw.send_keys(Keys.ENTER)

    fin = time.time() + 60
    while time.time() < fin:
        time.sleep(2)
        if d.execute_script(JS_FILAS):
            print("login OK")
            return
    shot(d, "err_post_login")
    raise SystemExit("Login sin lista de moviles. Revisa data/err_post_login.png")

# ------------------------------------------------------------------ scrape

def leer_movil(d, patente):
    fila = d.find_element(By.CSS_SELECTOR, f'[data-km="{patente}"]')
    d.execute_script("arguments[0].scrollIntoView({block:'center'});", fila)
    time.sleep(0.2)

    d.execute_script(JS_CLICK, d.execute_script(JS_FLECHA, fila))
    time.sleep(PAUSA)

    if not d.execute_script(JS_ITEM, ["Información del móvil", "Informacion del movil"]):
        raise RuntimeError("no abrio menu contextual")
    time.sleep(PAUSA)

    km = ult = None
    t0 = time.time()
    while time.time() - t0 < ESPERA_MODAL:
        km = d.execute_script(JS_CAMPO, ["Kilometraje"])
        if km:
            ult = d.execute_script(JS_CAMPO, ["Último reporte", "Ultimo reporte"])
            break
        time.sleep(0.4)

    if not d.execute_script(JS_CERRAR):
        try:
            d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
    time.sleep(PAUSA)
    return km, ult

def num(s):
    if not s:
        return None
    s = re.sub(r"[^\d.,]", "", s)
    if s.count(",") and s.count("."):
        s = s.replace(".", "").replace(",", ".")
    elif s.count(","):
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

# ------------------------------------------------------------------ main

def main():
    if not USER or not PASS:
        raise SystemExit("Faltan secrets HAWK_USER / HAWK_PASS")

    os.makedirs(OUT_DIR, exist_ok=True)
    d = crear_driver()
    try:
        login(d)
        pats = d.execute_script(JS_FILAS)
        if MAX_MOVILES:
            pats = pats[:MAX_MOVILES]
        print(f"{len(pats)} moviles detectados")

        ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))
        filas = []
        for i, p in enumerate(pats, 1):
            km = ult = err = None
            for intento in (1, 2):
                try:
                    d.execute_script(JS_FILAS)
                    km, ult = leer_movil(d, p)
                    err = None
                    break
                except Exception as e:
                    err = str(e)[:120]
                    time.sleep(1)
            filas.append({
                "Patente": p,
                "Kilometraje": num(km),
                "Km_raw": km,
                "Ultimo_reporte": ult,
                "Fecha_lectura": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                "Error": err,
            })
            print(f"[{i}/{len(pats)}] {p} -> {km} {'ERR:' + err if err else ''}")

        df = pd.DataFrame(filas)
        stamp = ahora.strftime("%Y%m%d_%H%M")
        df.to_excel(f"{OUT_DIR}/kilometrajes_{stamp}.xlsx", index=False)
        df.to_excel(f"{OUT_DIR}/kilometrajes_latest.xlsx", index=False)

        hist = f"{OUT_DIR}/historico.csv"
        df.to_csv(hist, mode="a", header=not os.path.exists(hist), index=False)

        ok = df["Kilometraje"].notna().sum()
        print(f"\nOK {ok}/{len(df)} -> {OUT_DIR}/kilometrajes_{stamp}.xlsx")
        if ok == 0:
            shot(d, "err_sin_datos")
            sys.exit(1)
    except SystemExit:
        shot(d, "err_fatal")
        raise
    except Exception:
        traceback.print_exc()
        shot(d, "err_fatal")
        sys.exit(1)
    finally:
        d.quit()

if __name__ == "__main__":
    main()
