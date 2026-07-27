#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de monitoreo de precios de vuelos (SerpApi Google Flights + Telegram).

Monitoriza dos opciones de vuelo a Tenerife y avisa por Telegram cuando
el precio TOTAL de ida + vuelta baja del minimo historico registrado.

Para cada opcion busca los tramos de ida y vuelta por separado, encuentra
los numeros de vuelo exactos y suma sus precios.

Necesita la variable de entorno SERPAPI_API_KEY.
"""
import os
import sys
import json
import argparse
import time
from datetime import datetime, timezone
import requests

# CONFIGURACION

FLIGHT_OPTIONS = [
    {
        "name": "Vueling",
        "outbound": {"flight_number": "VY3216", "from": "BCN", "to": "TFN", "date": "2026-09-23", "time": "15:50"},
        "return": {"flight_number": "VY3209", "from": "TFN", "to": "BCN", "date": "2026-09-27", "time": "20:15"},
        "baseline_price": 158.0,
    },
    {
        "name": "Iberia Express",
        "outbound": {"flight_number": "I21561", "from": "MAD", "to": "TFN", "date": "2026-09-23", "time": "06:40"},
        "return": {"flight_number": "I21586", "from": "TFN", "to": "MAD", "date": "2026-09-27", "time": "21:05"},
        "baseline_price": 141.0,
    },
]

HISTORY_FILE = "flight_price_history_v2.json"
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
    if price_value is None:
        return None
    if isinstance(price_value, (int, float)):
        return float(price_value)
    s = str(price_value)
    for ch in "€$£ ":
        s = s.replace(ch, "")
    for word in ["EUR", "USD", "GBP"]:
        s = s.replace(word, "")
    s = s.strip()
    if not s:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if len(s.split(",")[-1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

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


def search_one_way(date, from_airport, to_airport, api_key):
    params = {
        "engine": "google_flights",
        "departure_id": from_airport,
        "arrival_id": to_airport,
        "outbound_date": date,
        "currency": "EUR",
        "hl": "es",
        "gl": "es",
        "api_key": api_key,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def find_flight_in_data(data, flight_number):
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            flights = itinerary.get("flights", [])
            if not flights:
                continue
            flight = flights[0]
            if flight.get("flight_number") == flight_number:
                return {
                    "price": parse_price(itinerary.get("price")),
                    "flight": flight,
                }
    return None


def search_round_trip(option, api_key):
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


def find_matching_round_trip(data, option):
    wanted_out = option["outbound"]["flight_number"]
    wanted_ret = option["return"]["flight_number"]
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            flights = itinerary.get("flights", [])
            if len(flights) < 2:
                continue
            out_fn = flights[0].get("flight_number", "")
            ret_fn = flights[1].get("flight_number", "")
            if out_fn == wanted_out and ret_fn == wanted_ret:
                return {
                    "price": parse_price(itinerary.get("price")),
                    "outbound": flights[0],
                    "return": flights[1],
                    "match": "round-trip exacto",
                }
    return None


def get_cheapest_round_trip(data):
    candidates = []
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            price = parse_price(itinerary.get("price"))
            if price is not None:
                candidates.append((price, itinerary))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        itinerary = candidates[0][1]
        flights = itinerary.get("flights", [])
        return {
            "price": candidates[0][0],
            "outbound": flights[0] if len(flights) > 0 else None,
            "return": flights[1] if len(flights) > 1 else None,
            "match": "vuelo redondo mas barato (fallback)",
        }
    return None


def format_flight_line(leg, option_leg, emoji):
    if leg is None:
        return f"{emoji} {option_leg['flight_number']} {option_leg['from']} -> {option_leg['to']}"
    dep = leg.get("departure_airport", {}).get("id", option_leg["from"])
    arr = leg.get("arrival_airport", {}).get("id", option_leg["to"])
    fn = leg.get("flight_number", option_leg["flight_number"])
    return f"{emoji} {fn} {dep} -> {arr}"


def check_option(option, api_key, history):
    option_name = option["name"]
    opt_hist = init_option_history(history, option_name, option["baseline_price"])

    print(f"\n[{option_name}] Buscando tramos exactos...")

    # Buscar ida y vuelta por separado
    out_data = search_one_way(option["outbound"]["date"], option["outbound"]["from"], option["outbound"]["to"], api_key)
    time.sleep(2)
    ret_data = search_one_way(option["return"]["date"], option["return"]["from"], option["return"]["to"], api_key)
    time.sleep(2)

    out_match = find_flight_in_data(out_data, option["outbound"]["flight_number"])
    ret_match = find_flight_in_data(ret_data, option["return"]["flight_number"])

    result = None

    if out_match and ret_match and out_match["price"] is not None and ret_match["price"] is not None:
        total = out_match["price"] + ret_match["price"]
        result = {
            "price": total,
            "outbound": out_match["flight"],
            "return": ret_match["flight"],
            "out_price": out_match["price"],
            "ret_price": ret_match["price"],
            "match": "ida + vuelta exactos",
        }
        print(f"  Tramo ida: €{out_match['price']:.2f}")
        print(f"  Tramo vuelta: €{ret_match['price']:.2f}")
        print(f"  TOTAL exacto: €{total:.2f}")
    else:
        print(f"  No se encontraron ambos tramos exactos. Fallback a redondo...")
        rt_data = search_round_trip(option, api_key)
        result = find_matching_round_trip(rt_data, option)
        if result is None:
            result = get_cheapest_round_trip(rt_data)
        if result:
            print(f"  TOTAL redondo (fallback): €{result['price']:.2f} ({result['match']})")

    if result is None or result["price"] is None:
        print(f"  No se pudo obtener precio para {option_name}.")
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return False

    price = result["price"]
    now_iso = datetime.now(timezone.utc).isoformat()
    opt_hist["last_price"] = price
    opt_hist["last_check_at"] = now_iso
    opt_hist["checks"].append({
        "at": now_iso,
        "price": price,
        "match": result["match"],
        "out_price": result.get("out_price"),
        "ret_price": result.get("ret_price"),
    })
    opt_hist["checks"] = opt_hist["checks"][-30:]

    print(f"  Mejor precio historico: €{opt_hist['best_price']:.2f}")

    if price < opt_hist["best_price"]:
        previous_best = opt_hist["best_price"]
        ahorro = previous_best - price
        opt_hist["best_price"] = price
        opt_hist["best_price_at"] = now_iso

        out_line = format_flight_line(result.get("outbound"), option["outbound"], "🛫")
        ret_line = format_flight_line(result.get("return"), option["return"], "🛬")

        detalle_tramos = ""
        if "out_price" in result and "ret_price" in result:
            detalle_tramos = (
                f"\n🛫 Ida: €{result['out_price']:.2f}\n"
                f"🛬 Vuelta: €{result['ret_price']:.2f}\n"
            )

        msg = (
            f"✈️ *Bajada de precio: {option_name}*\n\n"
            f"💶 Precio total actual: *€{price:.2f}*\n"
            f"📉 Mejor precio anterior: €{previous_best:.2f}\n"
            f"💰 Ahorro: *€{ahorro:.2f}*"
            f"{detalle_tramos}\n"
            f"{out_line}\n"
            f"{ret_line}\n\n"
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
        time.sleep(3)

    save_history(history)

    print(f"\nTotal alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
