#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de monitoreo de precios de vuelos (SerpApi Google Flights + Telegram).

Monitoriza dos opciones de vuelo a Tenerife, envia un resumen cada 8 horas
y avisa cuando el precio total redondo baja del minimo historico.

Necesita la variable de entorno SERPAPI_API_KEY.
"""
import os
import sys
import json
import argparse
import time
from datetime import datetime, timezone
from collections import defaultdict
import requests

# CONFIGURACION DE VUELOS EXACTOS SOLICITADOS
# Option 1: Iberia Express (MAD 06:40 -> TFN 08:30 / TFN 21:05 -> MAD 00:50)
# Option 2: Vueling Airlines (BCN 07:20 -> TFN 09:55 / TFN 20:15 -> BCN 00:35)

FLIGHT_OPTIONS = [
    {
        "name": "Vueling",
        "outbound": {"flight_number": "VY3212", "from": "BCN", "to": "TFN", "date": "2026-09-23", "time": "07:20"},
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
DAYS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]


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
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            return True
        print(f"Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error enviando a Telegram: {e}", file=sys.stderr)
        return False


def search_round_trip(option, api_key, max_retries=3):
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
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == max_retries:
                raise e
            time.sleep(2 * attempt)


def normalize_flight_number(fn):
    if not fn:
        return ""
    return "".join(ch for ch in str(fn).upper() if ch.isalnum())


def flight_matches(flight, wanted_fn):
    if not flight:
        return False
    wanted = normalize_flight_number(wanted_fn)
    fn = normalize_flight_number(flight.get("flight_number", ""))
    if fn and (fn == wanted or wanted.endswith(fn)):
        return True
    carrier = flight.get("airline", "") or flight.get("carrier_code", "")
    full = normalize_flight_number(carrier + flight.get("flight_number", ""))
    return full == wanted


def leg_matches(leg, wanted_leg):
    if not leg or not wanted_leg:
        return False

    # Verificar hora de salida si se especifica en los parámetros deseados
    wanted_time = wanted_leg.get("time")
    if wanted_time:
        dep_time_str = leg.get("departure_airport", {}).get("time", "") or leg.get("time", "")
        if wanted_time not in dep_time_str:
            return False

    # Verificar numero de vuelo o aerolinea
    wanted_fn = wanted_leg.get("flight_number")
    if wanted_fn:
        fn_match = flight_matches(leg, wanted_fn)
        if not fn_match:
            carrier = (leg.get("airline", "") + " " + leg.get("carrier_code", "")).lower()
            wanted_carrier = wanted_fn[:2].lower()
            if wanted_carrier not in carrier and wanted_fn.lower() not in carrier:
                return False

    return True


def find_matching_round_trip(data, option):
    exact_candidates = []
    partial_candidates = []

    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            flights = itinerary.get("flights", [])
            return_flights = itinerary.get("return_flights", [])

            out = flights[0] if flights else None
            if return_flights:
                ret = return_flights[0]
            elif len(flights) > 1:
                ret = flights[1]
            else:
                ret = None

            out_ok = leg_matches(out, option["outbound"])
            ret_ok = leg_matches(ret, option["return"])

            raw_price = itinerary.get("price") or itinerary.get("extracted_price")
            price = parse_price(raw_price)

            if price is not None:
                if out_ok and ret_ok:
                    exact_candidates.append((price, itinerary, out, ret, "vuelos exactos"))
                elif out_ok:
                    partial_candidates.append((price, itinerary, out, ret, "ida exacta"))

    if exact_candidates:
        exact_candidates.sort(key=lambda x: x[0])
        price, itinerary, out, ret, match = exact_candidates[0]
        return {
            "price": price,
            "outbound": out,
            "return": ret,
            "match": match,
        }

    if partial_candidates:
        partial_candidates.sort(key=lambda x: x[0])
        price, itinerary, out, ret, match = partial_candidates[0]
        return {
            "price": price,
            "outbound": out,
            "return": ret,
            "match": match,
        }

    return None


def airline_name_matches(flight, name):
    if not flight:
        return False
    full_name = (flight.get("airline", "") + " " + flight.get("airline_logo", "")).lower()
    code = flight.get("carrier_code", "").lower()
    target = name.lower()
    if target in full_name:
        return True
    mappings = {
        "vueling": ["vueling", "vy"],
        "iberia express": ["iberia express", "iberia_express", "i2", "iberia"],
    }
    for alias in mappings.get(target, [target]):
        if alias in full_name or alias == code:
            return True
    return False


def get_cheapest_airline_round_trip(data, airline_name):
    candidates = []
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            outbound_flights = itinerary.get("flights", [])
            return_flights = itinerary.get("return_flights", [])
            all_flights = outbound_flights + return_flights
            if len(all_flights) < 2 and len(outbound_flights) < 2:
                continue
            flights_to_check = all_flights if all_flights else outbound_flights
            if any(airline_name_matches(f, airline_name) for f in flights_to_check):
                raw_price = itinerary.get("price") or itinerary.get("extracted_price")
                price = parse_price(raw_price)
                if price is not None:
                    candidates.append((price, itinerary))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        itinerary = candidates[0][1]
        outbound_flights = itinerary.get("flights", [])
        return_flights = itinerary.get("return_flights", [])
        out = outbound_flights[0] if outbound_flights else None
        ret = return_flights[0] if return_flights else (outbound_flights[1] if len(outbound_flights) > 1 else None)
        return {
            "price": candidates[0][0],
            "outbound": out,
            "return": ret,
            "match": f"mas barato {airline_name} (fallback)",
        }
    return None


def get_cheapest_round_trip(data):
    candidates = []
    for key in ["best_flights", "other_flights", "top_flights"]:
        if key not in data or not isinstance(data[key], list):
            continue
        for itinerary in data[key]:
            raw_price = itinerary.get("price") or itinerary.get("extracted_price")
            price = parse_price(raw_price)
            if price is not None:
                candidates.append((price, itinerary))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        itinerary = candidates[0][1]
        outbound_flights = itinerary.get("flights", [])
        return_flights = itinerary.get("return_flights", [])
        out = outbound_flights[0] if outbound_flights else None
        ret = return_flights[0] if return_flights else (outbound_flights[1] if len(outbound_flights) > 1 else None)
        return {
            "price": candidates[0][0],
            "outbound": out,
            "return": ret,
            "match": "vuelo redondo mas barato (fallback)",
        }
    return None


def format_flight_line(leg, option_leg, emoji):
    if leg is None:
        return f"{emoji} {option_leg['flight_number']} {option_leg['from']} -> {option_leg['to']} ({option_leg.get('time', '')})"
    dep = leg.get("departure_airport", {}).get("id", option_leg["from"])
    arr = leg.get("arrival_airport", {}).get("id", option_leg["to"])
    carrier = leg.get("carrier_code", leg.get("airline", ""))
    fn = leg.get("flight_number", option_leg["flight_number"])
    full_fn = f"{carrier}{fn}".strip()
    time_str = leg.get("departure_airport", {}).get("time", option_leg.get("time", ""))
    if " " in time_str:
        time_str = time_str.split(" ")[-1]
    return f"{emoji} {full_fn} {dep} ({time_str}) -> {arr}"


def build_google_flights_url(option):
    origin = option["outbound"]["from"]
    dest = option["outbound"]["to"]
    out_date = option["outbound"]["date"]
    ret_date = option["return"]["date"]
    query = f"Flights from {origin} to {dest} on {out_date} through {ret_date}"
    return (
        f"https://www.google.com/travel/flights/search?"
        f"hl=es&curr=EUR&q={requests.utils.quote(query)}"
    )


def analyze_cheapest_times(checks, min_checks=5):
    """Analiza checks para encontrar hora y dia mas baratos promedio si hay al menos min_checks."""
    if not checks or len(checks) < min_checks:
        return (None, None), (None, None)

    by_hour = defaultdict(list)
    by_weekday = defaultdict(list)

    for check in checks:
        try:
            dt = datetime.fromisoformat(check["at"])
            by_hour[dt.hour].append(check["price"])
            by_weekday[dt.weekday()].append(check["price"])
        except (ValueError, KeyError):
            continue

    cheapest_hour = None
    cheapest_hour_avg = None
    if by_hour:
        hour_avgs = {h: sum(prices)/len(prices) for h, prices in by_hour.items() if prices}
        if hour_avgs:
            cheapest_hour = min(hour_avgs, key=hour_avgs.get)
            cheapest_hour_avg = hour_avgs[cheapest_hour]

    cheapest_day = None
    cheapest_day_avg = None
    if by_weekday:
        day_avgs = {d: sum(prices)/len(prices) for d, prices in by_weekday.items() if prices}
        if day_avgs:
            cheapest_day_idx = min(day_avgs, key=day_avgs.get)
            cheapest_day = DAYS_ES[cheapest_day_idx]
            cheapest_day_avg = day_avgs[cheapest_day_idx]

    return (cheapest_hour, cheapest_hour_avg), (cheapest_day, cheapest_day_avg)


def check_option(option, api_key, history):
    option_name = option["name"]
    opt_hist = init_option_history(history, option_name, option["baseline_price"])

    print(f"\n[{option_name}] Buscando vuelo redondo {option['outbound']['from']}->{option['outbound']['to']} ...")

    try:
        data = search_round_trip(option, api_key)
    except Exception as e:
        print(f"  ERROR consultando SerpApi: {e}", file=sys.stderr)
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return None

    result = find_matching_round_trip(data, option)
    if result is None:
        result = get_cheapest_airline_round_trip(data, option_name)
    if result is None:
        result = get_cheapest_round_trip(data)

    if result is None or result["price"] is None:
        print(f"  No se pudo obtener precio para {option_name}.")
        opt_hist["last_check_at"] = datetime.now(timezone.utc).isoformat()
        return None

    price = result["price"]
    now_iso = datetime.now(timezone.utc).isoformat()
    opt_hist["last_price"] = price
    opt_hist["last_check_at"] = now_iso
    opt_hist["checks"].append({
        "at": now_iso,
        "price": price,
        "match": result["match"],
    })
    opt_hist["checks"] = opt_hist["checks"][-90:]

    previous_best = opt_hist["best_price"]
    price_dropped = price < previous_best
    if price_dropped:
        opt_hist["best_price"] = price
        opt_hist["best_price_at"] = now_iso

    print(f"  Precio encontrado: €{price:.2f} ({result['match']})")
    print(f"  Mejor precio historico: €{opt_hist['best_price']:.2f}")
    if price_dropped:
        print(f"  🚨 Nueva bajada detectada")

    return {
        "option": option,
        "price": price,
        "previous_best": previous_best,
        "best_price": opt_hist["best_price"],
        "best_price_at": opt_hist["best_price_at"],
        "price_dropped": price_dropped,
        "match": result["match"],
        "outbound": result.get("outbound"),
        "return": result.get("return"),
    }


def build_summary_message(history, results):
    ahora = datetime.now(timezone.utc)
    lines = [f"📊 *Resumen de vuelos - {ahora.strftime('%d/%m/%Y %H:%M')} UTC*\n"]

    valid_results = [r for r in results if r is not None]
    any_drop = any(r.get("price_dropped", False) for r in valid_results)
    if any_drop:
        lines.append("🚨 *Se detectaron nuevas bajadas de precio*\n")

    for i, r in enumerate(results):
        option = FLIGHT_OPTIONS[i]
        option_name = option["name"]

        if r is None:
            lines.append(
                f"⚠️ *{option_name}* ({option['outbound']['flight_number']} + {option['return']['flight_number']})\n"
                f"❌ Error consultando la API de SerpApi.\n"
            )
            continue

        opt_hist = history.get(option_name, {})
        checks = opt_hist.get("checks", [])
        (cheapest_hour, cheapest_hour_avg), (cheapest_day, cheapest_day_avg) = analyze_cheapest_times(checks)

        out_line = format_flight_line(r.get("outbound"), option["outbound"], "🛫")
        ret_line = format_flight_line(r.get("return"), option["return"], "🛬")

        drop_line = ""
        if r["price_dropped"]:
            savings = r["previous_best"] - r["price"] if r["previous_best"] else 0
            drop_line = f"\n🚨 *BAJADA: -€{savings:.2f}*"

        best_at_str = ""
        if r.get("best_price_at"):
            try:
                dt = datetime.fromisoformat(r["best_price_at"])
                best_at_str = dt.strftime('%d/%m %H:%M')
            except ValueError:
                pass

        hour_line = ""
        if cheapest_hour is not None:
            hour_line = f"\n⏰ Hora mas barata: *{cheapest_hour:02d}:00* (prom. €{cheapest_hour_avg:.2f})"

        day_line = ""
        if cheapest_day is not None:
            day_line = f"\n📅 Dia mas barato: *{cheapest_day}* (prom. €{cheapest_day_avg:.2f})"

        url = build_google_flights_url(option)

        lines.append(
            f"✈️ *{option_name}* ({option['outbound']['flight_number']} + {option['return']['flight_number']})"
            f"{drop_line}\n"
            f"💶 Precio actual: *€{r['price']:.2f}*\n"
            f"📉 Mejor precio: €{r['best_price']:.2f} ({best_at_str})"
            f"{hour_line}"
            f"{day_line}\n"
            f"{out_line}\n"
            f"{ret_line}\n"
            f"🔗 [Ver en Google Flights]({url})\n"
        )

    lines.append(f"\n🕐 {ahora.strftime('%d/%m/%Y %H:%M')} UTC")
    return "\n".join(lines)


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
    results = []

    for option in FLIGHT_OPTIONS:
        result = check_option(option, api_key, history)
        results.append(result)
        time.sleep(3)

    save_history(history)

    # Enviar resumen siempre
    summary = build_summary_message(history, results)
    if summary:
        if enviar_telegrama(summary):
            print("\n✅ Resumen enviado.")
        else:
            print("\n❌ Fallo enviando resumen.", file=sys.stderr)
            sys.exit(1)
    else:
        print("\nNo se genero resumen.")


if __name__ == "__main__":
    main()
