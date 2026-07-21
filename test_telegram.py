#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script standalone para probar la conexion con Telegram.

Uso local:
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export TELEGRAM_CHAT_ID="123456789"
    python test_telegram.py

Uso en GitHub Actions: ejecuta el workflow "Test Telegram" manualmente.
"""
import os
import sys
import requests
from datetime import datetime


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
    print(f"Usando chat_id: {chat_id}")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("ok") is True:
            print("Mensaje enviado correctamente.")
            return True
        else:
            print(f"Telegram API error: {resp.status_code} {resp.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error enviando a Telegram: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    mensaje = (
        "\U0001f7e2 *Prueba de conexion exitosa*\n\n"
        "Si ves este mensaje, la configuracion de Telegram esta correcta.\n"
        f"\U0001f550 {datetime.now().strftime('%d/%m/%Y %H:%M')} UTC"
    )
    ok = enviar_telegrama(mensaje)
    sys.exit(0 if ok else 1)
