#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import requests

PRICE_THRESHOLD = 62000

def obtener_precio_btc():
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            timeout=10,
            headers={"User-Agent": "btc-alert-bot/1.0"}
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data["bitcoin"]["usd"])
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
        return float(data["price"])
    except Exception as e:
        print(f"Binance falló: {e}", file=sys.stderr)
    return None

def enviar_telegrama(mensaje):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: faltan secrets TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
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

def main():
    precio = obtener_precio_btc()
    if precio is None:
        print("No se pudo obtener el precio de BTC desde ninguna fuente.", file=sys.stderr)
        sys.exit(1)
    print(f"Precio BTC: ${precio:,.2f} USD")
    if precio < PRICE_THRESHOLD:
        mensaje = (
            f"\U0001f6a8 *BTC por debajo de ${PRICE_THRESHOLD:,}*\n\n"
            f"Precio actual: *${precio:,.2f} USD*\n"
            f"Fuente: api publica"
        )
        ok = enviar_telegrama(mensaje)
        if ok:
            print("Notificacion enviada.")
        else:
            sys.exit(1)
    else:
        print("Por encima del umbral. Sin notificacion.")

if __name__ == "__main__":
    main()
