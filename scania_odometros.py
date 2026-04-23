"""
scania_odometros.py
Usa la API REST de Scania (rFMS) para obtener odómetros.
Sin Selenium, sin browser — llamadas HTTP directas.
"""

import os
import re
import json
import tempfile
import requests
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ─── CREDENCIALES DESDE GITHUB SECRETS ──────────────────────────────────────
SCANIA_CLIENT_ID     = os.environ["SCANIA_CLIENT_ID"]
SCANIA_CLIENT_SECRET = os.environ["SCANIA_CLIENT_SECRET"]
SHEET_ID             = os.environ["SHEET_ID"]
GOOGLE_CREDS         = os.environ["GOOGLE_CREDENTIALS_JSON"]

# ─── ENDPOINTS SCANIA API ────────────────────────────────────────────────────
TOKEN_URL    = "https://id.scania.com/auth/realms/fg-ext/protocol/openid-connect/token"
VEHICLES_URL = "https://api.scania.com/rfms/v4/vehicles"
STATUS_URL   = "https://api.scania.com/rfms/v4/vehiclestatuses"

# ─── CONFIGURACIÓN SHEETS ────────────────────────────────────────────────────
PESTANAS = [
    "Services-LAD",
    "Services-BUE",
    "Services-CAT",
    "Services-COR",
    "Services-LRJ",
    "Services-TUC",
]

COL_PATENTE   = 0  # Columna A
COL_KM_ACTUAL = 7  # Columna H


# ─── HELPERS ────────────────────────────────────────────────────────────────
def normalizar_patente(texto: str) -> str:
    return re.sub(r"\s+", "", str(texto)).upper().strip()


# ─── PASO 1: OBTENER TOKEN ───────────────────────────────────────────────────
def obtener_token() -> str:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Obteniendo token OAuth...")

    resp = requests.post(TOKEN_URL, data={
        "grant_type":    "client_credentials",
        "client_id":     SCANIA_CLIENT_ID,
        "client_secret": SCANIA_CLIENT_SECRET,
    }, timeout=30)

    print(f"  Token response: {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Error: {resp.text[:300]}")
        raise Exception(f"Error obteniendo token: {resp.status_code}")

    token = resp.json().get("access_token")
    print(f"  ✅ Token obtenido")
    return token


# ─── PASO 2: EXTRAER ODÓMETROS ──────────────────────────────────────────────
def extraer_odometros_scania() -> dict:
    token = obtener_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    odometros = {}

    # ── Obtener lista de vehículos ──────────────────────────────────────────
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Obteniendo lista de vehículos...")
    resp_v = requests.get(VEHICLES_URL, headers=headers, timeout=30)
    print(f"  Vehicles response: {resp_v.status_code}")

    vehiculos = []
    if resp_v.status_code == 200:
        data_v = resp_v.json()
        print(f"  Respuesta: {json.dumps(data_v)[:500]}")
        vehiculos = (
            data_v.get("vehicles") or
            data_v.get("Vehicle") or
            data_v.get("VehicleResponse", {}).get("Vehicle", []) or
            []
        )
        print(f"  Vehículos: {len(vehiculos)}")
        for v in vehiculos:
            print(f"    {json.dumps(v)[:150]}")
    else:
        print(f"  Error: {resp_v.text[:300]}")

    # ── Obtener estado actual (odómetro) ────────────────────────────────────
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Obteniendo odómetros...")
    resp_s = requests.get(STATUS_URL, headers=headers,
                          params={"latestOnly": "true"}, timeout=30)
    print(f"  Status response: {resp_s.status_code}")

    vin_a_km = {}
    if resp_s.status_code == 200:
        data_s = resp_s.json()
        print(f"  Respuesta (1000 chars): {json.dumps(data_s)[:1000]}")

        statuses = (
            data_s.get("vehicleStatuses") or
            data_s.get("VehicleStatus") or
            data_s.get("VehicleStatusResponse", {}).get("VehicleStatus", []) or
            []
        )

        for s in statuses:
            vin = s.get("vin") or s.get("Vin", "")
            km = 0

            # Odómetro viene en metros — dividir por 1000
            acum = s.get("accumulatedData") or s.get("AccumulatedData") or {}
            km_metros = (
                acum.get("totalVehicleDistance") or
                acum.get("TotalVehicleDistance") or
                s.get("hrTotalVehicleDistance") or
                s.get("HrTotalVehicleDistance") or
                0
            )
            if km_metros:
                km = int(km_metros) // 1000

            if vin and km:
                vin_a_km[vin] = km
                print(f"  VIN {vin}: {km:,} km")
    else:
        print(f"  Error: {resp_s.text[:300]}")

    # ── Cruzar VIN → patente ────────────────────────────────────────────────
    if vin_a_km and vehiculos:
        for v in vehiculos:
            vin = v.get("vin") or v.get("Vin", "")
            patente_raw = (
                v.get("externalId") or
                v.get("licensePlate") or
                v.get("name") or
                v.get("ExternalId") or
                ""
            )
            patente = normalizar_patente(patente_raw)
            if patente and vin in vin_a_km:
                odometros[patente] = vin_a_km[vin]
                print(f"  ✅ {patente}: {vin_a_km[vin]:,} km")
    elif vin_a_km:
        # Si no hay lista de vehículos, usar VIN como clave
        odometros = vin_a_km

    print(f"\nTotal Scania: {len(odometros)} vehículos")
    return odometros


# ─── PASO 3: ACTUALIZAR SHEETS ───────────────────────────────────────────────
def actualizar_sheets(odometros: dict):
    if not odometros:
        print("⚠️  Sin datos para actualizar.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Conectando a Google Sheets...")

    creds_dict = json.loads(GOOGLE_CREDS)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(creds_dict, f)
        creds_path = f.name

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SHEET_ID)
    os.unlink(creds_path)

    total = 0
    no_encontrados = []

    for nombre in PESTANAS:
        try:
            ws    = sheet.worksheet(nombre)
            datos = ws.get_all_values()
            print(f"\n  📋 {nombre} ({len(datos)-1} filas)")

            batch = []
            for idx, fila in enumerate(datos[1:], start=2):
                if not fila or not fila[COL_PATENTE].strip():
                    continue
                patente = normalizar_patente(fila[COL_PATENTE])
                if patente in odometros:
                    from gspread.utils import rowcol_to_a1
                    km_scania = odometros[patente]
                    km_sheet_str = fila[COL_KM_ACTUAL].strip() if len(fila) > COL_KM_ACTUAL else ""
                    km_sheet = int(km_sheet_str.replace(".", "").replace(",", "")) if km_sheet_str.isdigit() else 0
                    if km_scania > km_sheet:
                        celda = rowcol_to_a1(idx, COL_KM_ACTUAL + 1)
                        batch.append({"range": celda, "values": [[km_scania]]})
                        print(f"    ✅ {fila[COL_PATENTE]} → {km_scania:,} km")
                        total += 1
                    else:
                        print(f"    ⏭️  {fila[COL_PATENTE]} sin cambio (Sheet: {km_sheet:,} ≥ Scania: {km_scania:,})")
                else:
                    no_encontrados.append(f"{nombre}: {fila[COL_PATENTE]}")

            if batch:
                ws.batch_update(batch)

        except gspread.exceptions.WorksheetNotFound:
            print(f"  ❌ Pestaña '{nombre}' no encontrada")
        except Exception as e:
            print(f"  ❌ Error en '{nombre}': {e}")

    print(f"\n{'='*50}")
    print(f"✅ Scania actualizados: {total} vehículos")
    if no_encontrados:
        print(f"⚠️  No encontrados ({len(no_encontrados)}):")
        for p in no_encontrados[:10]:
            print(f"   - {p}")
    print(f"{'='*50}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  SCANIA API → GOOGLE SHEETS")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}")

    odometros = extraer_odometros_scania()

    if odometros:
        actualizar_sheets(odometros)
    else:
        print("\n⚠️  Sin datos de Scania — puede que los camiones estén apagados.")
