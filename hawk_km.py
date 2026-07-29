#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAWK GPS -> kilometraje de cada movil -> Excel
Headless en GitHub Actions.  Secrets: HAWK_USER, HAWK_PASS
"""

import os, re, sys, time, datetime, traceback
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

URL         = "http://www.hawkgps.com.ar/HawkEyeWeb/Index.aspx"
USER        = os.environ.get("HAWK_USER", "")
PASS        = os.environ.get("HAWK_PASS", "")
MAX_MOVILES = int(os.environ.get("MAX_MOVILES", "0"))
HEADLESS    = os.environ.get("HEADLESS", "1") == "1"

OUT_DIR = "data"
PAUSA   = 0.7
ESPERA  = 12

# ------------------------------------------------------------------ JS

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

JS_PREPARAR = """
const pat = arguments[0];
let nodos = [...document.querySelectorAll('div,li,td,tr')].filter(e => {
  if (e.offsetParent === null) return false;
  const r = e.getBoundingClientRect();
  if (r.width < 120 || r.width > 800) return false;
  if (r.height < 22 || r.height > 120) return false;
  const t = e.innerText || '';
  return t.includes(pat) && t.length < 90;
});
if (!nodos.length) return -1;
nodos.sort((a,b) => {
  const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
  return (ra.width*ra.height) - (rb.width*rb.height);
});
const fila = nodos[0];
fila.scrollIntoView({block:'center'});
window.__fila = fila;
const c = [...fila.querySelectorAll('*')].filter(e => {
  const r = e.getBoundingClientRect();
  return e.offsetParent !== null && r.width > 5 && r.width < 70 && r.height > 5 && r.height < 70;
});
c.sort((a,b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
window.__cands = c;
return c.length;
"""

JS_CLICK_CAND = """
const i = arguments[0];
const el = (i < 0) ? window.__fila : (window.__cands || [])[i];
if (!el) return false;
['mouseover','mousedown','mouseup','click'].forEach(n =>
  el.dispatchEvent(new MouseEvent(n, {bubbles:true, cancelable:true, view:window})));
return true;
"""

JS_CLICK_ESPECIAL = """
const el = window.__fila;
if (!el) return false;
if (arguments[0] === 'dbl')
  el.dispatchEvent(new MouseEvent('dblclick', {bubbles:true, cancelable:true, view:window}));
else
  el.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true, view:window, button:2}));
return true;
"""

JS_MENU_ABIERTO = """
const txts = ['Información del móvil','Informacion del movil'];
return [...document.querySelectorAll('div,li,a,span,td')]
  .some(e => e.children.length === 0 && e.offsetParent !== null &&
             txts.includes((e.innerText||'').trim()));
"""

JS_CLICK_MENU = """
const txts = ['Información del móvil','Informacion del movil'];
const els = [...document.querySelectorAll('div,li,a,span,td')]
  .filter(e => e.children.length === 0 && e.offsetParent !== null &&
               txts.includes((e.innerText||'').trim()));
if (!els.length) return false;
const el = els[els.length-1];
['mouseover','mousedown','mouseup','click'].forEach(n =>
  el.dispatchEvent(new MouseEvent(n, {bubbles:true, cancelable:true, view:window})));
return true;
"""

# ---- lectura de campo: texto en ancestros, luego inputs, luego body
JS_VALOR = """
const etiqueta = arguments[0];   // "Kilometraje" | "reporte"
const modo     = arguments[1];   // "num" | "txt"

const reNum = new RegExp(etiqueta + "[^0-9A-Za-z]{0,20}([0-9][0-9.,]*)");
const reTxt = new RegExp(etiqueta + "[^0-9]{0,15}([0-9]{1,2}\\\\/[0-9]{1,2}\\\\/[0-9]{2,4}[^\\\\n]{0,12})");
const re = (modo === "num") ? reNum : reTxt;

function probar(txt) {
  if (!txt) return null;
  const m = txt.match(re);
  return m ? m[1].trim() : null;
}

const hojas = [...document.querySelectorAll('div,td,span,label,li,p,b,font')]
  .filter(e => e.offsetParent !== null && (e.innerText||'').includes(etiqueta));

// 1) texto de la etiqueta y de sus ancestros
for (const h of hojas) {
  let n = h;
  for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
    const v = probar(n.innerText);
    if (v) return v;
  }
}
// 2) valores dentro de inputs cercanos
for (const h of hojas) {
  let n = h;
  for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
    const ins = [...n.querySelectorAll('input,textarea')]
      .map(e => (e.value || '').trim()).filter(Boolean);
    for (const v of ins) {
      if (modo === "num" && /^[0-9][0-9.,]*$/.test(v)) return v;
      if (modo === "txt" && /[0-9]{1,2}\\/[0-9]{1,2}\\/[0-9]{2,4}/.test(v)) return v;
    }
  }
}
// 3) todo el body
return probar(document.body.innerText);
"""

JS_MODAL_ABIERTO = """
return [...document.querySelectorAll('div,td,span,label,li,p,b,font')]
  .some(e => e.offsetParent !== null && (e.innerText||'').includes('Kilometraje'));
"""

JS_CERRAR_UNO = """
const c = [...document.querySelectorAll('div,span,a,img,button')]
  .filter(e => e.offsetParent !== null &&
    ((e.innerText||'').trim() === '\\u00d7' || (e.innerText||'').trim() === 'X' ||
     /close|cerrar/i.test(e.className + ' ' + (e.title||'') + ' ' + (e.id||''))));
if (!c.length) return false;
const el = c[c.length-1];
['mousedown','mouseup','click'].forEach(ev =>
  el.dispatchEvent(new MouseEvent(ev, {bubbles:true, cancelable:true, view:window})));
return true;
"""

JS_DUMP_MODAL = """
const hojas = [...document.querySelectorAll('div,td,span,label,li,p,b,font')]
  .filter(e => e.offsetParent !== null && (e.innerText||'').includes('Kilometraje'));
if (!hojas.length) return "SIN MODAL";
let n = hojas[0];
for (let i = 0; i < 6 && n.parentElement; i++) n = n.parentElement;
return "=== TEXTO ===\\n" + n.innerText +
       "\\n\\n=== HTML ===\\n" + n.outerHTML.slice(0, 150000);
"""

JS_DUMP_FILA = "return window.__fila ? window.__fila.outerHTML : 'sin fila';"

# ------------------------------------------------------------------ util

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

def guardar(nombre, texto):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(f"{OUT_DIR}/{nombre}", "w") as f:
            f.write(texto or "")
    except Exception:
        pass

def num(s):
    if not s:
        return None
    s = re.sub(r"[^\d.,]", "", s)
    if s.count(",") and s.count("."):
        s = s.replace(".", "").replace(",", ".")
    elif s.count(","):
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return None

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
            return
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
            print("login OK"); return
    shot(d, "err_post_login"); raise SystemExit("Login sin lista de moviles.")

# ------------------------------------------------------------------ helpers

def buscar_filtro(d):
    for e in d.find_elements(By.CSS_SELECTOR, "input"):
        try:
            if not e.is_displayed():
                continue
            ph = (e.get_attribute("placeholder") or "") + " " + (e.get_attribute("title") or "")
            if re.search(r"filtr", ph, re.I):
                return e
        except Exception:
            pass
    return None

def filtrar(d, filtro, texto):
    if filtro is None:
        return
    try:
        filtro.clear()
        filtro.send_keys(Keys.BACK_SPACE * 15)
        filtro.send_keys(texto)
    except Exception:
        pass
    time.sleep(1.2)

def cerrar_modal(d, intentos=5):
    for _ in range(intentos):
        if not d.execute_script(JS_MODAL_ABIERTO):
            return True
        d.execute_script(JS_CERRAR_UNO)
        time.sleep(0.4)
        try:
            d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
        time.sleep(0.4)
    return not d.execute_script(JS_MODAL_ABIERTO)

IDX_OK = None

def abrir_menu(d, patente):
    global IDX_OK
    n = d.execute_script(JS_PREPARAR, patente)
    if n == -1:
        raise RuntimeError("fila no encontrada")
    orden = ([IDX_OK] if IDX_OK is not None else []) + list(range(min(n, 8))) + [-1]
    for idx in orden:
        if not d.execute_script(JS_CLICK_CAND, idx):
            continue
        time.sleep(PAUSA)
        if d.execute_script(JS_MENU_ABIERTO):
            IDX_OK = idx
            return
    for tipo in ("dbl", "ctx"):
        d.execute_script(JS_CLICK_ESPECIAL, tipo)
        time.sleep(PAUSA)
        if d.execute_script(JS_MENU_ABIERTO):
            return
    guardar("fila_debug.html", d.execute_script(JS_DUMP_FILA))
    raise RuntimeError(f"no abrio menu (cands={n})")

def leer_movil(d, filtro, patente, dump=False):
    if not cerrar_modal(d):
        raise RuntimeError("modal anterior no cierra")

    filtrar(d, filtro, patente)
    abrir_menu(d, patente)

    if not d.execute_script(JS_CLICK_MENU):
        raise RuntimeError("no clickeo item del menu")

    km = ult = None
    t0 = time.time()
    while time.time() - t0 < ESPERA:
        if d.execute_script(JS_MODAL_ABIERTO):
            km = d.execute_script(JS_VALOR, "Kilometraje", "num")
            if km:
                break
        time.sleep(0.5)

    if d.execute_script(JS_MODAL_ABIERTO):
        ult = d.execute_script(JS_VALOR, "reporte", "txt")
        if dump:
            guardar("modal_debug.txt", d.execute_script(JS_DUMP_MODAL))

    cerrar_modal(d)
    return km, ult

# ------------------------------------------------------------------ main

def main():
    if not USER or not PASS:
        raise SystemExit("Faltan secrets HAWK_USER / HAWK_PASS")
    os.makedirs(OUT_DIR, exist_ok=True)
    d = crear_driver()
    try:
        login(d)
        pats = d.execute_script(JS_PATENTES)
        if MAX_MOVILES:
            pats = pats[:MAX_MOVILES]
        print(f"{len(pats)} moviles detectados")

        filtro = buscar_filtro(d)
        print("filtro:", "OK" if filtro is not None else "NO ENCONTRADO")

        ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))
        filas = []
        for i, p in enumerate(pats, 1):
            km = ult = err = None
            for intento in (1, 2):
                try:
                    km, ult = leer_movil(d, filtro, p, dump=(i == 1))
                    err = None
                    break
                except Exception as e:
                    err = str(e).split("\n")[0][:120]
                    time.sleep(1)
            filas.append({
                "Patente": p,
                "Kilometraje": num(km),
                "Km_raw": km,
                "Ultimo_reporte": ult,
                "Fecha_lectura": ahora.strftime("%Y-%m-%d %H:%M:%S"),
                "Error": err,
            })
            print(f"[{i}/{len(pats)}] {p} -> km={km} ult={ult} {'ERR:' + err if err else ''}")
            if i == 1:
                shot(d, "primer_movil")

        filtrar(d, filtro, "")
        df = pd.DataFrame(filas)
        stamp = ahora.strftime("%Y%m%d_%H%M")
        df.to_excel(f"{OUT_DIR}/kilometrajes_{stamp}.xlsx", index=False)
        df.to_excel(f"{OUT_DIR}/kilometrajes_latest.xlsx", index=False)
        hist = f"{OUT_DIR}/historico.csv"
        df.to_csv(hist, mode="a", header=not os.path.exists(hist), index=False)

        ok = int(df["Kilometraje"].notna().sum())
        unicos = df["Kilometraje"].dropna().nunique()
        print(f"\nOK {ok}/{len(df)}  valores_distintos={unicos}  idx_click={IDX_OK}")
        if ok == 0:
            shot(d, "err_sin_datos"); sys.exit(1)
    except SystemExit:
        shot(d, "err_fatal"); raise
    except Exception:
        traceback.print_exc(); shot(d, "err_fatal"); sys.exit(1)
    finally:
        d.quit()

if __name__ == "__main__":
    main()
