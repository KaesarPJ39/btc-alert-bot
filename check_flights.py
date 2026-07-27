#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de monitoreo de precios de vuelos (SerpApi Google Flights + Telegram).

Monitoriza dos opciones de vuelo a Tenerife y avisa por Telegram cuando
el precio total baja del minimo historico registrado.

Necesita la variable de entorno SERPAPI_API_KEY.
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone
import requests

# ══════════════════════════════════════════════════════════════
# CONFIGURACION
# ══════════════════════════════════════════════════════════════

FLIGHT_OPTIONS = [
    {
        "name": "Vueling",
        "outbound": {"flight_number": "VY3216", "from": "BCN", "to": "TFN", "date": "2026-09-23"},
        "return": {"flight_number": "VY3209", "from": "TFN", "to": "BCN", "date": "2026-09-27"},
        "baseline_price": 158.0,
    },
    {
        "name": "Iberia Express",
        "outbound": {"flight_number": "I21561", "from": "MAD", "to": "TFN", "date": "2026-09-23"},
        "return": {"flight_number": "I21586", "from": "TFN", "to": "MAD", "date": "2026-09-27"},
        "baseline_price": 141.0,
    },
]

HISTORY_FILE = "flight_price_history.json"
SERPAPI_URL = "https://serpapi.com/search.json"


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def init_option_history(history, option_name, baseline):
    if option_name not in history:
        history[option_name] = {
            "baseline": baseline,
            "best_price": baseline,
            "best_price_at": datetime.now(timezone.utc).isoformat(),
            "last_price": None,
            "last_check_at": None,
            "checks": [],
        }
    return history[option_name]


def parse_price(price_value):
    """Extrae un float de una cadena de precio como '158 EUR' o '€158'."""
    if price_value is None:
        return None
    if isinstance(price_value, (int, float)):
        return float(price_value)
    s = str(price_value)
    # Eliminar simbolos, espacios y palabras de moneda
    for ch in "€$£ ":
        s = s.replace(ch, "")
    for word in ["EUR", "USD", "GBP"]:
        s = s.replace(word, "")
    s = s.strip()
    if not s:
        return None

    # Detectar separador decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # "1.234,56" -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        else:
            # "1,234.56" -> 1234.56
            s = s.replace(",", "")
    elif "," in s:
        # Si hay 2 digitos despues de la coma, es decimal: "145,50" -> 145.50
        if len(s.split(",")[-1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    # Si solo hay punto, se asume decimal

    try:
        return float(s)
    except ValueError:
        return None


def enviar_telegrama(mensaje):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id_raw:
        print("ERROR: faltan secrets TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=sys.stderr)
        return False
    try:
        chat_id = int(chat_id_raw.strip())
    except ValueError:
        print(f"ERROR: TELEGRAM_CHAT_ID no es valido: '{chat_id_raw}'", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            return True
        print(f"Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error enviando a Telegram: {e}", file=sys.stderr)
        return False


def search_flights(option, api_key):
    params = {
        "engine": "google_flights",
        "departure_id": option["outbound"]["from"],
        "arrival_id": option["outbound"]["to"],
        "outbound_date": option["outbound"]["date"],
        "return_date": option["return"]["date"],
        "currency": "EUR",
        "hl": "es",
        "gl": "es",
        "api_key": api_key,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def find_matching_itinerary(data, option):
    """Busca en la respuesta de SerpApi un itinerario cuyos dos segmentos
    coincidan con los numeros de vuelo deseados."""
    wanted_out = option["outbound"]["flight_number"]
    wanted_ret = option["return"]["flight_number"]

    sections = []
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key in data and isinstance(data[key], list):
            sections.extend(data[key])

    for itinerary in sections:
        flights = itinerary.get("flights", [])
        if len(flights) < 2:
            continue
        out = flights[0]
        ret = flights[1]
        out_fn = out.get("flight_number", "")
        ret_fn = ret.get("flight_number", "")
        if out_fn == wanted_out and ret_fn == wanted_ret:
            return itinerary

    return None


def get_cheapest_airline_flight(data, airline_name):
    """Fallback: devuelve el itinerario mas barato de la aerolinea indicada."""
    sections = []
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key in data and isinstance(data[key], list):
            sections.extend(data[key])

    candidates = []
    for itinerary in sections:
        flights = itinerary.get("flights", [])
        if not flights:
            continue
        if any(airline_name.lower() in (f.get("airline", "") + f.get("airline_logo", "")).lower() for f in flights):
            price = parse_price(itinerary.get("price"))
            if price is not None:
                candidates.append((price, itinerary))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
    return None


def check_option(option, api_key, history):
    option_name = option["name"]
    opt_hist = init_option_history(history, option_name, option["baseline_price"])

    print(f"\n[{option_name}] Buscando {option['outbound']['from']}->{option['outbound']['to']} "
          f"({option['outbound']['date']}) + vuelta ({option['return']['date']})...")

    try:
        data = search_flights(option, api_key)
    except Exception as e:
        print(f"  ERROR consultando SerpApi: {e}", file=sys.stderr)
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return False

    itinerary = find_matching_itinerary(data, option)
    match_type = "vuelos exactos"

    if itinerary is None:
        itinerary = get_cheapest_airline_flight(data, option_name)
        match_type = f"aerolinea {option_name} (fallback)"

    if itinerary is None:
        print(f"  No se encontro itinerario para {option_name}.")
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return False

    price = parse_price(itinerary.get("price"))
    if price is None:
        print(f"  No se pudo parsear precio: {itinerary.get('price')}")
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    opt_hist["last_price"] = price
    opt_hist["last_check_at"] = now_iso
    opt_hist["checks"].append({"at": now_iso, "price": price, "match": match_type})
    # Mantener solo los ultimos 30 registros para no hacer crecer el archivo
    opt_hist["checks"] = opt_hist["checks"][-30:]

    print(f"  Precio encontrado: €{price:.2f} ({match_type})")
    print(f"  Mejor precio historico: €{opt_hist['best_price']:.2f}")

    if price < opt_hist["best_price"]:
        ahorro = opt_hist["best_price"] - price
        ahorro_total = ahorro  # por persona / reserva
        opt_hist["best_price"] = price
        opt_hist["best_price_at"] = now_iso

        out = itinerary["flights"][0]
        ret = itinerary["flights"][1]

        msg = (
            f"✈️ *Bajada de precio: {option_name}*\n\n"
            f"💶 Precio actual: *€{price:.2f}*\n"
            f"📉 Mejor precio anterior: €{opt_hist['best_price']:.2f}\n"
            f"💰 Ahorro: *€{ahorro_total:.2f}*\n\n"
            f"🛫 Ida: {out.get('flight_number', '?')} "
            f"{out.get('departure_airport', {}).get('id', option['outbound']['from'])} "
            f"-> {out.get('arrival_airport', {}).get('id', option['outbound']['to'])}\n"
            f"🛬 Vuelta: {ret.get('flight_number', '?')} "
            f"{ret.get('departure_airport', {}).get('id', option['return']['from'])} "
            f"-> {ret.get('arrival_airport', {}).get('id', option['return']['to'])}\n\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC"
        )
        if enviar_telegrama(msg):
            print(f"  ✅ Alerta enviada: €{ahorro:.2f} de ahorro")
            return True
        else:
            print(f"  ❌ Fallo enviando alerta", file=sys.stderr)
            return False

    print(f"  Sin cambios.")
    return False


def main():
    parser = argparse.ArgumentParser(description="Bot de monitoreo de precios de vuelos")
    parser.add_argument("--test", action="store_true", help="Envia un mensaje de prueba a Telegram")
    args = parser.parse_args()

    if args.test:
        msg = (
            "✈️ *Prueba de conexion - Flight Monitor*\n\n"
            "El monitor de vuelos puede enviar mensajes a este chat.\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC"
        )
        ok = enviar_telegrama(msg)
        print("Mensaje de prueba enviado." if ok else "Fallo el envio.")
        sys.exit(0 if ok else 1)

    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        print("ERROR: falta la variable de entorno SERPAPI_API_KEY.", file=sys.stderr)
        print("Obten una clave gratuita en https://serpapi.com y agregala a los secrets de GitHub.", file=sys.stderr)
        sys.exit(1)

    history = load_history()
    alerts_sent = 0

    for option in FLIGHT_OPTIONS:
        if check_option(option, api_key, history):
            alerts_sent += 1
        # Pequena pausa para no saturar SerpApi
        import time
        time.sleep(2)

    save_history(history)

    print(f"\nTotal alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
