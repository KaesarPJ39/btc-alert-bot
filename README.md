# Bot de Alertas Bitcoin (Telegram)

Bot que te avisa por Telegram cuando el precio de Bitcoin cae por debajo de $62.000 USD.
100% gratis, funciona online 24/7 sin necesidad de tu PC gracias a GitHub Actions.

## Como configurar

### 1. Crear el bot en Telegram

1. Abre Telegram y busca `@BotFather`.
2. Envia `/newbot` y sigue los pasos para crear un bot nuevo.
3. Guarda el **token** que te da (formato `123456789:ABCdefGhI...`).
4. Busca `@userinfobot` y envia `/start` para obtener tu **chat_id** (numero).

### 2. Agregar secrets al repositorio

En GitHub, ve a `Settings` > `Secrets and variables` > `Actions` > `New repository secret`:

- `TELEGRAM_BOT_TOKEN` → el token de tu bot
- `TELEGRAM_CHAT_ID` → tu chat_id numerico

### 3. Probar manualmente

En la pestana `Actions` de tu repositorio, selecciona el workflow **BTC Price Alert** y haz clic en **Run workflow**. Revisa Telegram para ver si llega el mensaje (si BTC esta por debajo de $62.000).

### 4. Automático

Una vez en la rama por defecto, el cron empezara a ejecutarse cada 5 minutos automaticamente. Puedes ver el historial en la pestana `Actions`.

## Cambiar el umbral

Edita la constante `PRICE_THRESHOLD` en `check_btc.py` (valor en USD).

## Como funciona

- Cada 5 minutos GitHub Actions ejecuta `check_btc.py`.
- Consulta el precio via CoinGecko API (sin API key necesaria).
- Si CoinGecko falla, usa Binance API como respaldo automatico.
- Si el precio esta por debajo del umbral, envia un mensaje a tu Telegram.
- Mientras BTC este debajo del umbral, recibiras un aviso cada 5 minutos.
