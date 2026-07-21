#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import argparse
from datetime import datetime, timezone
import requests

# ══════════════════════════════════════════════════════════════
# CONFIGURACION - Modifica estos valores segun tu posicion
# ══════════════════════════════════════════════════════════════

WBIT_ENTRY_PRICE = 13.57          # Precio al que compraste WBIT (EUR)
WBIT_STOP_LOSS = 13.00            # Stop-loss absoluto (EUR)
WBIT_ENTRY_ZONE_PCT = 2.0         # % de margen para alerta de zona de compra
WBIT_MOMENTUM_EUR = 0.50          # Subida intraday en EUR para alerta de momentum
BTC_DAILY_MOVE_PCT = 2.0          # % de movimiento diario de BTC para alertar
WBIT_TICKER = "WBTC.PA"           # Yahoo Finance ticker de WBIT en EUR

# ══════════════════════════════════════════════════════════════

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".alert_state.json")


def cargar_estado():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def guardar_estado(estado):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(estado, f)
    except Exception:
        pass


def limpiar_estado_si_nuevo_dia(estado):
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if estado.get("fecha") != hoy:
        return {"fecha": hoy}
    return estado


def obtener_precio_btc():
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,eur",
            timeout=10,
            headers={"User-Agent": "btc-alert-bot/1.0"}
        )
        resp.raise_for_status()
        data = resp.json()
        return {"usd": float(data["bitcoin"]["usd"]), "eur": float(data["bitcoin"]["eur"])}
    except Exception as e:
        print(f"CoinGecko falló: {e}", file=sys.stderr)
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            timeout=10,
            headers={"User-Agent": "btc-alert-bot/1.0"}
        )
        resp.raise_for_status()
        data = resp.json()
        return {"usd": float(data["price"]), "eur": None}
    except Exception as e:
        print(f"Binance falló: {e}", file=sys.stderr)
    return None


def obtener_precio_wbit():
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{WBIT_TICKER}?interval=1d&range=1d",
            timeout=10,
            headers={"User-Agent": "btc-alert-bot/1.0"}
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        precio_actual = meta["regularMarketPrice"]
        precio_apertura = meta.get("chartPreviousClose") or meta.get("regularMarketOpen", precio_actual)

        day_high = meta.get("regularMarketDayHigh", precio_actual)
        day_low = meta.get("regularMarketDayLow", precio_actual)

        return {
            "precio": float(precio_actual),
            "apertura": float(precio_apertura),
            "high": float(day_high),
            "low": float(day_low),
            "moneda": meta.get("currency", "EUR"),
        }
    except Exception as e:
        print(f"Yahoo Finance falló para {WBIT_TICKER}: {e}", file=sys.stderr)
    return None


def enviar_telegrama(mensaje):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id_raw:
        print("ERROR: faltan secrets TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)
    try:
        chat_id = int(chat_id_raw.strip())
    except ValueError:
        print(f"ERROR: TELEGRAM_CHAT_ID no es un numero valido: '{chat_id_raw}'", file=sys.stderr)
        sys.exit(1)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            return True
        else:
            print(f"Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error enviando a Telegram: {e}", file=sys.stderr)
        return False


def calcular_pct(valor_inicial, valor_actual):
    if valor_inicial == 0:
        return 0
    return ((valor_actual - valor_inicial) / abs(valor_inicial)) * 100


def check_alertas():
    ahora = datetime.now(timezone.utc)
    estado = cargar_estado()
    estado = limpiar_estado_si_nuevo_dia(estado)
    alertas_enviadas = []

    # ── Obtener precios ──────────────────────────────────────
    btc = obtener_precio_btc()
    wbit = obtener_precio_wbit()

    if btc is None and wbit is None:
        print("No se pudo obtener ningun precio.", file=sys.stderr)
        sys.exit(1)

    # ── Log de precios ───────────────────────────────────────
    if btc:
        print(f"BTC: ${btc['usd']:,.2f} USD", end="")
        if btc["eur"]:
            print(f" / €{btc['eur']:,.2f} EUR", end="")
        print()
    if wbit:
        pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        print(f"WBIT: €{wbit['precio']:.3f} (open €{wbit['apertura']:.3f}) "
              f"{pnl_emoji} P&L: {pnl:+.2f}%")

    # ── ALERTA 1: Stop-loss WBIT ─────────────────────────────
    if wbit and wbit["precio"] < WBIT_STOP_LOSS:
        pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
        perdida_eur = wbit["precio"] - WBIT_ENTRY_PRICE
        msg = (
            f"🚨 *STOP-LOSS WBIT*\n\n"
            f"Precio: *€{wbit['precio']:.3f}*\n"
            f"Stop-loss: €{WBIT_STOP_LOSS:.2f}\n"
            f"Entrada: €{WBIT_ENTRY_PRICE}\n"
            f"Perdida: *{pnl:+.2f}%* (€{perdida_eur:+.3f}/part.)\n\n"
            f"⚠️ *Considera cerrar posicion*"
        )
        if enviar_telegrama(msg):
            alertas_enviadas.append("stop-loss")
            print("🚨 Alerta STOP-LOSS enviada")

    # ── ALERTA 2: Zona de entrada (precio compra ±2%) ────────
    if wbit:
        margen = WBIT_ENTRY_PRICE * (WBIT_ENTRY_ZONE_PCT / 100)
        if abs(wbit["precio"] - WBIT_ENTRY_PRICE) <= margen:
            pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
            msg = (
                f"⚠️ *WBIT en zona de entrada*\n\n"
                f"Precio: *€{wbit['precio']:.3f}*\n"
                f"Tu entrada: €{WBIT_ENTRY_PRICE}\n"
                f"P&L: *{pnl:+.2f}%*\n\n"
                f"Estas cerca de tu precio de compra."
            )
            if enviar_telegrama(msg):
                alertas_enviadas.append("zona-entrada")
                print("⚠️ Alerta zona de entrada enviada")

    # ── ALERTA 3: BTC movimiento diario >2% ──────────────────
    if btc and not estado.get("btc_daily_alert"):
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1",
                timeout=10,
                headers={"User-Agent": "btc-alert-bot/1.0"}
            )
            resp.raise_for_status()
            chart = resp.json()
            precios = [p[1] for p in chart["prices"]]
            precio_inicio = precios[0]
            precio_actual = btc["usd"]
            cambio_pct = calcular_pct(precio_inicio, precio_actual)

            if abs(cambio_pct) >= BTC_DAILY_MOVE_PCT:
                direccion = "📈 SUBIDA" if cambio_pct > 0 else "📉 BAJADA"
                msg = (
                    f"{direccion} *BTC >{BTC_DAILY_MOVE_PCT}% hoy*\n\n"
                    f"Inicio dia: *${precio_inicio:,.2f}*\n"
                    f"Ahora: *${precio_actual:,.2f}*\n"
                    f"Cambio: *{cambio_pct:+.2f}%*\n\n"
                    f"WBIT probablemente se mueva pronto."
                )
                if enviar_telegrama(msg):
                    estado["btc_daily_alert"] = True
                    alertas_enviadas.append("btc-diario")
                    print(f"📈 Alerta BTC diario ({cambio_pct:+.2f}%) enviada")
        except Exception as e:
            print(f"Error obteniendo historial BTC: {e}", file=sys.stderr)

    # ── ALERTA 4: Momentum WBIT (+€0.50 desde apertura) ──────
    if wbit and not estado.get("wbit_momentum"):
        subida = wbit["precio"] - wbit["apertura"]
        if subida >= WBIT_MOMENTUM_EUR:
            pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
            msg = (
                f"🚀 *WBIT Momentum fuerte*\n\n"
                f"Subida hoy: *+€{subida:.3f}* desde apertura\n"
                f"Precio actual: *€{wbit['precio']:.3f}*\n"
                f"Apertura: €{wbit['apertura']:.3f}\n"
                f"P&L desde compra: *{pnl:+.2f}%*\n\n"
                f"📈 Dia muy alcista."
            )
            if enviar_telegrama(msg):
                estado["wbit_momentum"] = True
                alertas_enviadas.append("momentum")
                print("🚀 Alerta momentum enviada")

    # ── ALERTA 5: Nuevo maximo del dia ───────────────────────
    if wbit:
        max_anterior = estado.get("wbit_day_high", 0)
        if wbit["high"] > max_anterior and max_anterior > 0:
            pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
            msg = (
                f"🆙 *WBIT nuevo maximo del dia*\n\n"
                f"Nuevo max: *€{wbit['high']:.3f}*\n"
                f"Precio actual: *€{wbit['precio']:.3f}*\n"
                f"P&L: *{pnl:+.2f}%*"
            )
            if enviar_telegrama(msg):
                alertas_enviadas.append("nuevo-max")
                print("🆙 Alerta nuevo maximo enviada")
        estado["wbit_day_high"] = max(max_anterior, wbit["high"])

    # ── ALERTA 6: Movimiento rapido >1% en 30min ─────────────
    if wbit and not estado.get("rapido_hoy"):
        precio_anterior = estado.get("wbit_precio_anterior")
        if precio_anterior and precio_anterior > 0:
            cambio = abs(calcular_pct(precio_anterior, wbit["precio"]))
            if cambio >= 1.0:
                direccion = "📈" if wbit["precio"] > precio_anterior else "📉"
                msg = (
                    f"⚡ *WBIT movimiento rapido*\n\n"
                    f"Hace ~5min: €{precio_anterior:.3f}\n"
                    f"Ahora: *€{wbit['precio']:.3f}*\n"
                    f"Cambio: *{cambio:.2f}%* {direccion}\n\n"
                    f"Volatilidad inusual, revisa tu posicion."
                )
                if enviar_telegrama(msg):
                    estado["rapido_hoy"] = True
                    alertas_enviadas.append("rapido")
                    print("⚡ Alerta movimiento rapido enviada")
        estado["wbit_precio_anterior"] = wbit["precio"]

    # ── ALERTA 7: Resumen diario a las 20:00 UTC ─────────────
    hora_utc = ahora.hour
    minuto_utc = ahora.minute
    if hora_utc == 20 and minuto_utc < 5 and not estado.get("resumen_diario"):
        lineas = []
        if wbit:
            pnl = calcular_pct(WBIT_ENTRY_PRICE, wbit["precio"])
            pnl_eur = wbit["precio"] - WBIT_ENTRY_PRICE
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            lineas.append(
                f"*WBIT*\n"
                f"Precio cierre: *€{wbit['precio']:.3f}*\n"
                f"Max/Min dia: €{wbit['high']:.3f} / €{wbit['low']:.3f}\n"
                f"Apertura: €{wbit['apertura']:.3f}\n"
                f"{pnl_emoji} P&L: *{pnl:+.2f}%* (€{pnl_eur:+.3f}/part.)"
            )
        if btc:
            lineas.append(
                f"\n*BTC*\n"
                f"Precio: *${btc['usd']:,.2f} USD*"
            )
            if btc["eur"]:
                lineas[1] += f"\nPrecio: *€{btc['eur']:,.2f} EUR*"

        msg = (
            f"📊 *Resumen diario*\n\n"
            + "\n\n".join(lineas) +
            f"\n\n🕐 {ahora.strftime('%d/%m/%Y %H:%M')} UTC"
        )
        if enviar_telegrama(msg):
            estado["resumen_diario"] = True
            alertas_enviadas.append("resumen")
            print("📊 Resumen diario enviado")

    # ── Guardar estado ───────────────────────────────────────
    guardar_estado(estado)

    if not alertas_enviadas:
        print("Sin alertas. Todo normal.")

    return alertas_enviadas


def main():
    parser = argparse.ArgumentParser(description="Bot de alertas BTC/WBIT para Telegram")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Envia un mensaje de prueba inmediato a Telegram"
    )
    args = parser.parse_args()

    if args.test:
        msg = (
            "🟢 *Prueba de conexion exitosa*\n\n"
            "El bot puede enviar mensajes a este chat.\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC"
        )
        ok = enviar_telegrama(msg)
        print("Mensaje de prueba enviado." if ok else "Fallo el envio.")
        sys.exit(0 if ok else 1)

    check_alertas()


if __name__ == "__main__":
    main()
