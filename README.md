# Bot de Alertas BTC + WBIT + Vuelos (Telegram)

Bot que monitorea Bitcoin, WBIT (WisdomTree Physical Bitcoin ETP) y precios de vuelos, avisandote por Telegram cuando ocurre algo importante.

## Alertas configuradas

| Señal | Condición | Frecuencia |
|-------|-----------|------------|
| 🚨 Stop-loss | WBIT < €13.00 | Cada 5 min |
| ⚠️ Zona de entrada | WBIT dentro de ±2% de tu precio de compra | Cada 5 min |
| 📈 BTC diario | BTC se mueve >2% en el día | 1 vez/día |
| 🚀 Momentum | WBIT sube >€0.50 desde apertura | 1 vez/día |
| 🆕 Nuevo máximo | WBIT alcanza nuevo máximo del día | Al ocurrir |
| ⚡ Movimiento rápido | WBIT mueve >1% en ~5 min | 1 vez/día |
| 📊 Resumen diario | Resumen de tu posición a las 20:00 UTC | 1 vez/día |

## Configuración

### 1. Crear el bot en Telegram

1. Abre Telegram y busca `@BotFather`.
2. Envía `/newbot` y sigue los pasos.
3. Guarda el **token** (formato `123456789:ABCdefGhI...`).
4. Envía `/start` a tu bot desde Telegram.
5. Para obtener tu **chat_id**, abre en el navegador:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Busca `"chat":{"id":123456789}`.

### 2. Agregar secrets al repositorio

En GitHub: `Settings` → `Secrets and variables` → `Actions` → `New repository secret`:

- `TELEGRAM_BOT_TOKEN` → el token de tu bot
- `TELEGRAM_CHAT_ID` → tu chat_id numérico

### 3. Probar

- **Test Telegram**: workflow manual para verificar la conexion.
- **BTC Price Alert**: workflow manual para probar todas las alertas.
- **Flight Price Monitor**: workflow manual para probar el monitor de vuelos.

### 4. Monitor de vuelos

El bot tambien monitorea dos opciones de vuelo a Tenerife:

| Opcion | Ida | Vuelta | Precio actual |
|--------|-----|--------|---------------|
| Vueling | VY3216 BCN→TFN 23 Sep 2026 | VY3209 TFN→BCN 27 Sep 2026 | €158 |
| Iberia Express | I21561 MAD→TFN 23 Sep 2026 | I21586 TFN→MAD 27 Sep 2026 | €141 |

**Como activarlo:**

1. Registrate gratis en [SerpApi](https://serpapi.com) y copia tu API key.
2. En GitHub: `Settings` → `Secrets and variables` → `Actions` → `New repository secret`.
3. Agrega `SERPAPI_API_KEY` con tu clave.
4. Ejecuta manualmente el workflow **Flight Price Monitor** para probar.

Se ejecuta **2 veces al dia** (09:13 y 21:13 UTC) y avisa si el precio total baja del minimo historico registrado.

### 5. Personalizar alertas

Edita las constantes en `check_btc.py`:

```python
WBIT_ENTRY_PRICE = 13.57      # Tu precio de compra (EUR)
WBIT_STOP_LOSS = 13.00        # Stop-loss absoluto
WBIT_ENTRY_ZONE_PCT = 2.0     # % margen zona de entrada
WBIT_MOMENTUM_EUR = 0.50      # Subida intraday para alerta
BTC_DAILY_MOVE_PCT = 2.0      # % movimiento diario BTC
WBIT_TICKER = "WBTC.PA"       # Yahoo Finance ticker
```

## Cómo funciona

- Cada 5 minutos GitHub Actions ejecuta `check_btc.py`.
- Consulta BTC via CoinGecko (respaldo: Binance).
- Consulta WBIT via Yahoo Finance API.
- Evalúa todas las condiciones y envía alertas a Telegram.
- El estado se cachea entre ejecuciones para evitar alertas duplicadas.
