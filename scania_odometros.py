"""
scania_odometros.py
Autenticación Scania FMS API - Challenge/Response (3 pasos)
Sin Selenium — llamadas HTTP directas a dataaccess.scania.com
"""

import os
import re
import json
import hmac
import hashlib
import base64
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

# ─── ENDPOINTS SCANIA ────────────────────────────────────────────────────────
BASE_URL       = "https://dataaccess.scania.com"
CHALLENGE_URL  = f"{BASE_URL}/auth/clientid2challenge"
TOKEN_URL      = f"{BASE_URL}/auth/response2token"
POSITIONS_URL  = f"{BASE_URL}/rfms4/vehiclepositions"
VEHICLES_URL   = f"{BASE_URL}/rfms4/vehicles"

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

def base64url_decode(s: str) -> bytes:
    """Decodifica base64url (reemplaza - por + y _ por /)"""
    s = s.replace('-', '+').replace('_', '/')
    # Padding
    pad = 4 - len(s) % 4
    if pad != 4:
        s += '=' * pad
    return base64.b64decode(s)

def base64url_encode(b: bytes) -> str:
    """Codifica bytes a base64url (sin padding)"""
    s = base64.b64encode(b).decode('utf-8')
    s = s.rstrip('=')
    s = s.replace('+', '-').replace('/', '_')
    return s


# ─── PASO 1-2-3: OBTENER TOKEN ───────────────────────────────────────────────
def obtener_token() -> str:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Autenticando con Scania API...")

    # ── Step 1: Get Challenge ──────────────────────────────────────────────
    print("  Step 1: Obteniendo challenge...")
    resp1 = requests.post(
        CHALLENGE_URL,
        data={"clientId": SCANIA_CLIENT_ID},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30
    )
    print(f"  Step 1 response: {resp1.status_code}")

    if resp1.status_code != 200:
        raise Exception(f"Error Step 1: {resp1.status_code} — {resp1.text[:200]}")

    challenge = resp1.json().get("challenge")
    if not challenge:
        raise Exception(f"No se recibió challenge: {resp1.text[:200]}")
    print(f"  Challenge recibido: {challenge[:20]}...")

    # ── Step 2: Create Challenge Response ─────────────────────────────────
    print("  Step 2: Calculando HMAC-SHA256...")
    secret_bytes   = base64url_decode(SCANIA_CLIENT_SECRET)
    challenge_bytes = base64url_decode(challenge)
    hmac_result    = hmac.new(secret_bytes, challenge_bytes, hashlib.sha256).digest()
    challenge_response = base64url_encode(hmac_result)
    print(f"  Challenge response: {challenge_response[:20]}...")

    # ── Step 3: Get Token ──────────────────────────────────────────────────
    print("  Step 3: Obteniendo token...")
    resp3 = requests.post(
        TOKEN_URL,
        data={
            "clientId": SCANIA_CLIENT_ID,
            "Response": challenge_response,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30
    )
    print(f"  Step 3 response: {resp3.status_code}")

    if resp3.status_code != 200:
        raise Exception(f"Error Step 3: {resp3.status_code} — {resp3.text[:200]}")

    token = resp3.json().get("token")
    if not token:
        raise Exception(f"No se recibió token: {resp3.text[:200]}")

    print(f"  ✅ Token obtenido correctamente")
    return token


# ─── EXTRAER ODÓMETROS ───────────────────────────────────────────────────────
def extraer_odometros_scania() -> dict:
    token = obtener_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json; rfms=vehiclepositions.v4.0",
    }

    odometros = {}

    # ── Obtener posiciones con odómetro ────────────────────────────────────
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Obteniendo posiciones/odómetros...")
    resp = requests.get(
        POSITIONS_URL,
        headers=headers,
        params={"latestOnly": "true"},
        timeout=30
    )
    print(f"  Positions response: {resp.status_code}")

    if resp.status_code == 200:
        data = resp.json()
        print(f"  Respuesta (1000 chars): {json.dumps(data)[:1000]}")

        posiciones = (
            data.get("vehiclePositions") or
            data.get("VehiclePositionResponse", {}).get("VehiclePosition", []) or
            []
        )
        print(f"  Posiciones: {len(posiciones)}")

        for pos in posiciones:
            vin = pos.get("vin") or pos.get("Vin", "")

            # Odómetro — viene en metros
            km = 0
            km_metros = (
                pos.get("tachographSpeed") or  # no es esto
                pos.get("wheelBasedSpeed") or  # tampoco
                pos.get("gnssPosition", {}).get("altitude") or  # tampoco
                0
            )

            # Buscar odómetro en todos los campos posibles
            for campo in ["hrTotalVehicleDistance", "totalVehicleDistance",
                          "TotalVehicleDistance", "HrTotalVehicleDistance",
                          "odometer", "Odometer"]:
                val = pos.get(campo)
                if val and int(val) > 1000:
                    km = int(val) // 1000
                    break

            # También buscar dentro de accumulatedData
            acum = pos.get("accumulatedData") or pos.get("AccumulatedData") or {}
            if not km and acum:
                for campo in ["totalVehicleDistance", "TotalVehicleDistance",
                              "hrTotalVehicleDistance"]:
                    val = acum.get(campo)
                    if val and int(val) > 1000:
                        km = int(val) // 1000
                        break

            # Obtener patente — puede estar en externalId o en el VIN
            patente_raw = (
                pos.get("externalId") or
                pos.get("ExternalId") or
                pos.get("licensePlate") or
                pos.get("vehicleIdentificationNumber") or
                vin
            )
            patente = normalizar_patente(patente_raw) if patente_raw else None

            if patente and km:
                odometros[patente] = km
                print(f"  ✅ {patente}: {km:,} km")
            elif vin:
                print(f"  ⚠️  VIN {vin}: patente={patente}, km={km} — campos: {list(pos.keys())[:8]}")

    else:
        print(f"  Error: {resp.text[:300]}")

    # ── Si no encontró nada, intentar con vehicles ──────────────────────────
    if not odometros:
        print(f"\n  Intentando con /rfms4/vehicles...")
        headers_v = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json; rfms=vehicles.v4.0",
        }
        resp_v = requests.get(VEHICLES_URL, headers=headers_v, timeout=30)
        print(f"  Vehicles response: {resp_v.status_code}")
        if resp_v.status_code == 200:
            print(f"  Vehicles data: {resp_v.text[:500]}")

    print(f"\nTotal Scania: {len(odometros)} vehículos")
    return odometros


# ─── ACTUALIZAR SHEETS ───────────────────────────────────────────────────────
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
                    km_scania  = odometros[patente]
                    km_sheet_str = fila[COL_KM_ACTUAL].strip() if len(fila) > COL_KM_ACTUAL else ""
                    km_sheet   = int(km_sheet_str.replace(".", "").replace(",", "")) if km_sheet_str.isdigit() else 0
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
        print(f"⚠️  No encontrados en Scania ({len(no_encontrados)}):")
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
        print("\n⚠️  Sin datos de Scania.")
