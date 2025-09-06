# bot_listener.py
import os
import json
import time
import threading
import requests

print("🔥 Iniciando bot_listener")

# =========================
# Configuración principal
# =========================
BOT_TOKEN = "7674766197:AAEwF84b6WR40XWpbilzo5DpzgowKk1K454"  # tu token
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mini-API interna del bot (NO chocar con application.py en 5000)
LOCAL_FLASK_PORT = 5001
CRYPTO_API = f"http://127.0.0.1:{LOCAL_FLASK_PORT}/crypto-data-actual"

# Tu application.py imprime prefijo VACÍO => deja cadena vacía
API_PREFIX = ""
API_SERVER_PORT = 5000

ALARM_FILE = "alarmas.json"

# =========================
# Utilidades de alarmas (persistencia)
# =========================
if os.path.exists(ALARM_FILE):
    with open(ALARM_FILE, "r", encoding="utf-8") as f:
        alarmas = json.load(f)
else:
    alarmas = []

def guardar_alarmas():
    with open(ALARM_FILE, "w", encoding="utf-8") as f:
        json.dump(alarmas, f)

def get_updates(offset=None):
    try:
        response = requests.get(f"{API_URL}/getUpdates", params={"offset": offset, "timeout": 20}, timeout=30)
        return response.json()
    except Exception as e:
        print(f"⚠️ get_updates error: {e}")
        return {"result": []}

def send_message(chat_id, text):
    print(f"✉️ Enviando mensaje a {chat_id}: {text}")
    try:
        url = f"{API_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload, timeout=20)
        print(f"📡 Respuesta de Telegram: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"⚠️ Error send_message: {e}")

# =========================
# Helpers de EMA + Binance (tolerancia AUTO = 1 tick)
# =========================
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_ALLOWED_INTERVALS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d"}

_symbol_cache_global = {}   # base -> SYMBOLUSDT
_symbol_meta_global = {}    # SYMBOL -> {"tickSize": float}
_ema_check_last = {}        # throttle de chequeo por alarma

def _build_symbol_cache_global():
    """Carga símbolo base→SYMBOLUSDT una sola vez."""
    global _symbol_cache_global
    if _symbol_cache_global:
        return
    try:
        r = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=12)
        r.raise_for_status()
        data = r.json()
        for s in data.get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                _symbol_cache_global[s["baseAsset"].lower()] = s["symbol"]
    except Exception as e:
        print(f"⚠️ No se pudo construir caché de símbolos (EMA): {e}")

def _symbol_for(moneda: str):
    """Normaliza alias y devuelve SYMBOLUSDT o None."""
    alias_map = {
        "bitcoin": "btc", "ethereum": "eth", "binance coin": "bnb", "dogecoin": "doge",
        "solana": "sol", "cardano": "ada", "ripple": "xrp", "polkadot": "dot",
        "litecoin": "ltc", "tron": "trx"
    }
    base = alias_map.get(moneda.lower(), moneda.lower())
    _build_symbol_cache_global()
    return _symbol_cache_global.get(base)

def _get_tick_size(symbol: str):
    """Devuelve tickSize para SYMBOL (cachea). Si falla, None."""
    try:
        meta = _symbol_meta_global.get(symbol)
        if meta and "tickSize" in meta:
            return float(meta["tickSize"])
        # Cargar desde /exchangeInfo
        r = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=12)
        r.raise_for_status()
        data = r.json()
        for s in data.get("symbols", []):
            if s.get("symbol") == symbol:
                tick = None
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        tick = float(f.get("tickSize"))
                        break
                if tick is not None:
                    _symbol_meta_global[symbol] = {"tickSize": tick}
                    return tick
                break
    except Exception as e:
        print(f"⚠️ No se pudo obtener tickSize para {symbol}: {e}")
    return None

def _ema(values, period: int):
    """EMA clásica con semilla SMA."""
    k = 2.0 / (period + 1.0)
    ema_vals = []
    ema_prev = None
    for i, v in enumerate(values):
        if i < period - 1:
            ema_vals.append(float("nan"))
        elif i == period - 1:
            sma = sum(values[:period]) / float(period)
            ema_vals.append(sma)
            ema_prev = sma
        else:
            ema_t = (v * k) + (ema_prev * (1 - k))
            ema_vals.append(ema_t)
            ema_prev = ema_t
    return ema_vals

def _price_and_ema(symbol: str, intervalo: str, period: int):
    """Devuelve (precio_actual, ema_period) para símbolo/intervalo."""
    limit = max(period + 5, 50)
    r = requests.get(BINANCE_KLINES_URL, params={"symbol": symbol, "interval": intervalo, "limit": limit}, timeout=12)
    r.raise_for_status()
    data = r.json()
    closes = [float(k[4]) for k in data]
    ema_series = _ema(closes, period)
    return closes[-1], ema_series[-1]

# =========================
# Verificador de alarmas (precio, volumen, EMA)
# =========================
def verificar_alarmas_tick():
    # --- Alarmas de precio (CoinGecko)
    for alarma in alarmas[:]:
        if "precio_objetivo" in alarma:
            moneda = alarma["moneda"]
            chat_id = alarma["chat_id"]
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": moneda, "vs_currencies": "usd"},
                    timeout=20
                )
                if r.status_code == 200:
                    data = r.json()
                    precio_actual = data.get(moneda, {}).get("usd", 0)
                    if precio_actual >= alarma["precio_objetivo"]:
                        send_message(chat_id, f"🚨 ¡{moneda.upper()} ha alcanzado ${precio_actual:.2f}!")
                        alarmas.remove(alarma)
                        guardar_alarmas()
            except Exception as e:
                print(f"⚠️ Error verificando alarma de precio: {e}")

    # --- Alarmas de volumen (verde/rojo) usando mini-API interna
    for alarma in alarmas[:]:
        if "tipo_volumen" in alarma:
            moneda = alarma["moneda"]
            chat_id = alarma["chat_id"]
            tipo = alarma["tipo_volumen"]  # 'verde' | 'rojo'
            umbral = alarma.get("umbral", 30000 if tipo == "verde" else 60000)
            try:
                r = requests.get(CRYPTO_API, params={"name": moneda}, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    volumen = data.get("volumen_verde" if tipo == "verde" else "volumen_rojo", 0)
                    if volumen > umbral:
                        color = "🟢" if tipo == "verde" else "🔴"
                        send_message(chat_id, f"{color} ¡Alerta! Volumen {tipo.upper()} de {moneda.upper()} supera {umbral}: {volumen}")
                        alarmas.remove(alarma)
                        guardar_alarmas()
            except Exception as e:
                print(f"⚠️ Error verificando volumen: {e}")

    # --- Alarmas de EMA (precio toca EMA N con tolerancia 'auto' o manual)
    for alarma in alarmas[:]:
        if "ema_period" in alarma:
            moneda = alarma["moneda"]
            chat_id = alarma["chat_id"]
            intervalo = alarma.get("intervalo", "1h")
            periodo = int(alarma["ema_period"])
            tol_cfg = alarma.get("tolerancia", "auto")  # 'auto' o float (fracción)

            # throttle: 1 petición / 30s por alarma
            key = (chat_id, moneda, intervalo, periodo)
            now = time.time()
            if now - _ema_check_last.get(key, 0) < 30:
                continue
            _ema_check_last[key] = now

            try:
                symbol = _symbol_for(moneda)
                if not symbol:
                    continue
                price, ema_val = _price_and_ema(symbol, intervalo, periodo)
                if not (price and ema_val):
                    continue

                if isinstance(tol_cfg, str) and tol_cfg.lower() == "auto":
                    tick = _get_tick_size(symbol) or 0.0
                    touched = abs(price - ema_val) <= max(tick, 0.0)
                    tol_msg = "auto (1 tick)"
                else:
                    tol = float(tol_cfg)
                    touched = (abs(price - ema_val) / ema_val) <= tol
                    tol_msg = f"±{tol*100:.4f}%"

                if touched:
                    send_message(
                        chat_id,
                        f"📣 {moneda.upper()} {intervalo}: precio {price:.6f} tocó EMA{periodo} ({ema_val:.6f}) {tol_msg}"
                    )
                    alarmas.remove(alarma)
                    guardar_alarmas()

            except Exception as e:
                print(f"⚠️ Error verificando alarma EMA ({moneda} {intervalo} EMA{periodo}): {e}")

# =========================
# Mini-API Flask interna (5001)
# =========================
def run_flask():
    from flask import Flask, request, jsonify
    BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
    _symbol_cache = {}

    def _build_symbol_cache():
        nonlocal _symbol_cache
        try:
            r = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=12)
            r.raise_for_status()
            data = r.json()
            for s in data.get("symbols", []):
                if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                    base = s["baseAsset"].lower()
                    _symbol_cache[base] = s["symbol"]
        except Exception as e:
            print(f"⚠️ No se pudo construir caché de símbolos: {e}")

    app = Flask(__name__)
    _build_symbol_cache()

    @app.route('/crypto-data-actual')
    def crypto_data():
        """
        Devuelve volumen verde/rojo y precio actual (1h, últimas 10 velas) desde Binance.
        """
        name = request.args.get("name", "bitcoin").lower()
        alias_map = {
            "bitcoin": "btc",
            "ethereum": "eth",
            "binance coin": "bnb",
            "dogecoin": "doge",
            "solana": "sol",
            "cardano": "ada",
            "ripple": "xrp",
            "polkadot": "dot",
            "litecoin": "ltc",
            "tron": "trx",
        }
        name = alias_map.get(name, name)

        if not _symbol_cache:
            _build_symbol_cache()

        symbol = _symbol_cache.get(name)
        if not symbol:
            return jsonify({"error": "Moneda no soportada en Binance"}), 400

        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {"symbol": symbol, "interval": "1h", "limit": 10}
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()

            volumen_rojo = 0.0
            volumen_verde = 0.0
            precio_actual = float(data[-1][4])

            for vela in data:
                open_price = float(vela[1])
                close_price = float(vela[4])
                volume = float(vela[5])
                if close_price >= open_price:
                    volumen_verde += volume
                else:
                    volumen_rojo += volume

            return jsonify({
                "volumen_verde": volumen_verde,
                "volumen_rojo": volumen_rojo,
                "precio": precio_actual
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # IMPORTANTE: correr en 5001
    app.run(host="127.0.0.1", port=LOCAL_FLASK_PORT)

# =========================
# Lógica del Bot (comandos)
# =========================
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text.lower().startswith("/ayuda"):
        ayuda = (
            "🤖 *CriptoIQ — Ayuda rápida*\n"
            "\n"
            "Aquí tienes lo que puedo hacer. Usa los *formatos exactos* y los ejemplos al final 👇\n"
            "\n"
            "📈 *Gráficas y Análisis*\n"
            "• /EMA <moneda> <intervalo> <velas>\n"
            "  └ Gráfica con EMAs 20/50/100/200.\n"
            "• /precio <moneda> <días>\n"
            "  └ Resumen de precio (Coingecko) y volumen 1h (Binance).\n"
            "\n"
            "⏰ *Alarmas de Precio*\n"
            "• /alarma <moneda> <precio>\n"
            "  └ Te aviso cuando el precio *toque o supere* ese valor.\n"
            "• /eliminaralarma <moneda>\n"
            "  └ Elimina tu alarma de precio para esa moneda.\n"
            "\n"
            "📦 *Alarmas de Volumen*\n"
            "• /alarma volumen <moneda> <verde|rojo> <umbral>\n"
            "  └ Alerta cuando el *volumen verde/rojo 1h* supere el umbral.\n"
            "• /eliminarvolumen <moneda> <verde|rojo>\n"
            "  └ Elimina la alarma de volumen indicada.\n"
            "\n"
            "📐 *Alarmas EMA (toque de media exponencial)*\n"
            "• /alarma ema <moneda> <intervalo> <periodo> [tolerancia%|auto]\n"
            "  └ Te aviso cuando el precio *toque* la EMA indicada.\n"
            "  └ *tolerancia*: por defecto es *auto* (= 1 tick de Binance, lo más preciso posible).\n"
            "• /eliminaralarma ema <moneda> <intervalo> <periodo>\n"
            "  └ Elimina esa alarma EMA.\n"
            "\n"
            "🗂️ *Gestión*\n"
            "• /alarmas\n"
            "  └ Lista todas tus alarmas activas (precio, volumen y EMA).\n"
            "• /crypto precio <moneda>\n"
            "  └ Precio rápido + volumen actual (consulta interna).\n"
            "\n"
            "🧭 *Notas*\n"
            "• *Intervalos válidos:* 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d\n"
            "• Puedes usar nombres o símbolos comunes (ej: *bitcoin* o *btc*, *dogecoin* o *doge*).\n"
            "• Las alarmas se disparan *una vez* y luego se eliminan automáticamente.\n"
            "\n"
            "🧪 *Ejemplos*\n"
            "• /EMA doge 1h 50\n"
            "• /precio bitcoin 30\n"
            "• /alarma btc 65000\n"
            "• /alarma volumen eth verde 5000\n"
            "• /alarma ema doge 1h 50          ← tolerancia auto (1 tick)\n"
            "• /alarma ema doge 1h 50 0.10%     ← tolerancia manual\n"
            "• /eliminaralarma ema doge 1h 50\n"
            "• /alarmas\n"
        )
        send_message(chat_id, ayuda)


    elif text.lower().startswith("/ema"):
        partes = text.split()
        if len(partes) == 4:
            par = partes[1].lower()
            intervalo = partes[2]
            try:
                num_velas = int(partes[3])

                base_url = f"http://127.0.0.1:{API_SERVER_PORT}{API_PREFIX}/ema-graph"
                print(f"🔎 GET {base_url} par={par} intervalo={intervalo} num_velas={num_velas}")

                # Intento: pedir JSON para usar file_url (imagen guardada en static/ema/telegram/)
                r = requests.get(
                    base_url,
                    params={
                        "par": par,
                        "intervalo": intervalo,
                        "num_velas": num_velas,
                        "mode": "json",
                        "save_dir": "telegram"
                    },
                    timeout=20
                )

                if r.status_code == 200:
                    data = r.json()
                    file_url = data.get("file_url")

                    if file_url:
                        img = requests.get(f"http://127.0.0.1:5000{file_url}", timeout=20).content
                    else:
                        # Fallback: pedir PNG directo si no hay file_url
                        png = requests.get(
                            base_url,
                            params={"par": par, "intervalo": intervalo, "num_velas": num_velas},
                            timeout=20
                        )
                        png.raise_for_status()
                        img = png.content

                    files = {'photo': ('ema.png', img, 'image/png')}
                    requests.post(f"{API_URL}/sendPhoto", data={'chat_id': chat_id}, files=files)

                else:
                    try:
                        error_msg = r.json().get("error", "Error desconocido")
                    except:
                        error_msg = r.text or "Respuesta vacía o inesperada del servidor"
                    send_message(chat_id, f"❌ Error generando la gráfica: {error_msg}")

            except Exception as e:
                send_message(chat_id, f"⚠️ Error procesando el comando: {e}")
        else:
            send_message(chat_id, "❗ Usa el formato: /EMA <moneda> <intervalo> <número de velas>")

    # ---------- NUEVO: /alarma ema ----------
    elif text.lower().startswith("/alarma ema"):
        # /alarma ema <moneda> <intervalo> <periodoEMA> [tolerancia%|auto]
        partes = text.split()
        if len(partes) in (5, 6):
            _, _, moneda, intervalo, periodo_str, *rest = partes
            moneda = moneda.lower()
            try:
                if intervalo not in _ALLOWED_INTERVALS:
                    send_message(chat_id, f"⚠️ Intervalo inválido. Usa uno de: {', '.join(sorted(_ALLOWED_INTERVALS))}")
                    return
                periodo = int(periodo_str)
                if periodo <= 0:
                    raise ValueError()

                # Tolerancia: por defecto 'auto' (1 tick). Si pasan %, la usamos.
                tol = "auto"
                if rest:
                    raw = rest[0].strip().lower()
                    if raw != "auto":
                        t = raw.replace("%", "")
                        tol = float(t) / 100.0

                # Validar símbolo
                symbol = _symbol_for(moneda)
                if not symbol:
                    send_message(chat_id, f"⚠️ Moneda '{moneda}' no soportada en Binance/USDT.")
                    return

                # Si ya existe esa alarma, actualizar
                for a in alarmas:
                    if a["chat_id"] == chat_id and a.get("moneda") == moneda and a.get("ema_period") == periodo and a.get("intervalo") == intervalo:
                        a["tolerancia"] = tol
                        guardar_alarmas()
                        tol_txt = "auto (1 tick)" if (isinstance(tol, str) and tol == "auto") else f"±{tol*100:.4f}%"
                        send_message(chat_id, f"🔄 Alarma EMA{periodo} de {moneda.upper()} @ {intervalo} actualizada ({tol_txt}).")
                        return

                alarmas.append({
                    "chat_id": chat_id,
                    "moneda": moneda,
                    "intervalo": intervalo,
                    "ema_period": periodo,
                    "tolerancia": tol
                })
                guardar_alarmas()
                tol_txt = "auto (1 tick)" if (isinstance(tol, str) and tol == "auto") else f"±{tol*100:.4f}%"
                send_message(chat_id, f"⏰ Alarma creada: {moneda.upper()} toca EMA{periodo} en {intervalo} ({tol_txt}).")

            except Exception:
                send_message(chat_id, "⚠️ Formato: /alarma ema <moneda> <intervalo> <periodoEMA> [tolerancia%|auto]\nEj: /alarma ema doge 1h 50 auto")
        else:
            send_message(chat_id, "⚠️ Formato: /alarma ema <moneda> <intervalo> <periodoEMA> [tolerancia%|auto]")

    elif text.lower().startswith("/precio"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[1].lower()
            try:
                dias = int(partes[2])
                # Precio histórico (Coingecko)
                url = f"https://api.coingecko.com/api/v3/coins/{moneda}/market_chart"
                params = {"vs_currency": "usd", "days": dias}
                r = requests.get(url, params=params, timeout=20)
                precios = []
                if r.status_code == 200:
                    data = r.json()
                    precios = [p[1] for p in data.get("prices", []) if isinstance(p, list) and len(p) == 2]

                # Volumen actual (mini-API interna en 5001)
                r_vol = requests.get(CRYPTO_API, params={"name": moneda}, timeout=20)
                volumen_verde = volumen_rojo = None
                if r_vol.status_code == 200:
                    data_vol = r_vol.json()
                    volumen_verde = data_vol.get("volumen_verde", 0)
                    volumen_rojo = data_vol.get("volumen_rojo", 0)

                # Mensaje
                if precios:
                    max_price = max(precios)
                    min_price = min(precios)
                    actual = precios[-1]

                    pct_bajo_max = ((max_price - actual) / max_price) * 100 if max_price else 0
                    pct_sobre_min = ((actual - min_price) / min_price) * 100 if min_price else 0

                    if pct_bajo_max < 2:
                        max_label = "✅ muy cerca del máximo"
                    elif pct_bajo_max > 10:
                        max_label = "📉 lejos del máximo"
                    else:
                        max_label = "↔️ distancia moderada al máximo"

                    if pct_sobre_min > 30:
                        min_label = "📈 muy por encima del mínimo"
                    elif pct_sobre_min < 5:
                        min_label = "🔻 cerca del mínimo"
                    else:
                        min_label = "↔️ distancia moderada al mínimo"

                    msg = (
                        f"📊 {moneda.upper()} últimos {dias} días:\n"
                        f"💰 Actual: ${actual:.2f}\n"
                        f"📈 Máximo: ${max_price:.2f} ({pct_bajo_max:.2f}% por debajo) — {max_label}\n"
                        f"📉 Mínimo: ${min_price:.2f} ({pct_sobre_min:.2f}% por encima) — {min_label}"
                    )

                    if volumen_verde is not None and volumen_rojo is not None:
                        msg += (
                            f"\n\n📦 Volumen 1h (Binance):\n"
                            f"🟢 Verde: {volumen_verde:.2f}\n"
                            f"🔴 Rojo: {volumen_rojo:.2f}"
                        )
                else:
                    msg = "⚠️ No se encontraron datos válidos para el precio."

            except Exception as e:
                msg = f"⚠️ Error al obtener datos: {e}"

            send_message(chat_id, msg)

    elif text.lower().startswith("/alarma volumen"):
        partes = text.split()
        if len(partes) == 5:
            moneda, tipo, umbral = partes[2].lower(), partes[3].lower(), partes[4]
            try:
                umbral = float(umbral)
                if tipo not in ["verde", "rojo"]:
                    raise ValueError()
                for a in alarmas:
                    if a["chat_id"] == chat_id and a.get("moneda") == moneda and a.get("tipo_volumen") == tipo:
                        a["umbral"] = umbral
                        guardar_alarmas()
                        send_message(chat_id, f"🔄 Alarma de volumen {tipo.upper()} de {moneda.upper()} actualizada.")
                        return
                alarmas.append({
                    "chat_id": chat_id,
                    "moneda": moneda,
                    "tipo_volumen": tipo,
                    "umbral": umbral
                })
                guardar_alarmas()
                send_message(chat_id, f"📊 Alarma configurada para volumen {tipo.upper()} de {moneda.upper()} con umbral {umbral}")
            except:
                send_message(chat_id, "⚠️ Formato incorrecto. Usa: /alarma volumen <moneda> <verde|rojo> <umbral>")

    elif text.lower().startswith("/alarma"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[1].lower()
            try:
                precio = float(partes[2])
                for a in alarmas:
                    if a["chat_id"] == chat_id and a.get("moneda") == moneda and "precio_objetivo" in a:
                        a["precio_objetivo"] = precio
                        guardar_alarmas()
                        send_message(chat_id, f"🔄 Alarma de {moneda.upper()} actualizada a ${precio}")
                        return
                alarmas.append({
                    "chat_id": chat_id,
                    "moneda": moneda,
                    "precio_objetivo": precio
                })
                guardar_alarmas()
                send_message(chat_id, f"⏰ Alarma configurada para {moneda.upper()} a ${precio}")
            except:
                send_message(chat_id, "⚠️ Usa el formato: /alarma bitcoin 30000")

    elif text.lower().startswith("/eliminaralarma"):
        partes = text.split()

        # Forma EMA: /eliminaralarma ema <moneda> <intervalo> <periodo>
        if len(partes) == 5 and partes[1].lower() == "ema":
            _, _, moneda, intervalo, periodo_str = partes
            moneda = moneda.lower()
            try:
                periodo = int(periodo_str)
            except:
                send_message(chat_id, "⚠️ Periodo inválido.")
                return

            prev = len(alarmas)
            alarmas[:] = [
                a for a in alarmas
                if not (a["chat_id"] == chat_id and a.get("moneda") == moneda
                        and a.get("intervalo") == intervalo and a.get("ema_period") == periodo)
            ]
            if len(alarmas) < prev:
                guardar_alarmas()
                send_message(chat_id, f"🗑️ Alarma EMA{periodo} de {moneda.upper()} @ {intervalo} eliminada.")
            else:
                send_message(chat_id, f"⚠️ No se encontró esa alarma EMA.")

        # Forma precio: /eliminaralarma <moneda>
        elif len(partes) == 2:
            moneda = partes[1].lower()
            prev = len(alarmas)
            alarmas[:] = [a for a in alarmas if not (a["chat_id"] == chat_id and a.get("moneda") == moneda and "precio_objetivo" in a)]
            if len(alarmas) < prev:
                guardar_alarmas()
                send_message(chat_id, f"🗑️ Alarma de {moneda.upper()} eliminada.")
            else:
                send_message(chat_id, f"⚠️ No había alarma de {moneda.upper()}.")
        else:
            send_message(chat_id, "⚠️ Usa:\n• /eliminaralarma <moneda>\n• /eliminaralarma ema <moneda> <intervalo> <periodo>")

    elif text.lower().startswith("/eliminarvolumen"):
        partes = text.split()
        if len(partes) == 3:
            moneda, tipo = partes[1].lower(), partes[2].lower()
            prev = len(alarmas)
            alarmas[:] = [a for a in alarmas if not (a["chat_id"] == chat_id and a.get("moneda") == moneda and a.get("tipo_volumen") == tipo)]
            if len(alarmas) < prev:
                guardar_alarmas()
                send_message(chat_id, f"🗑️ Alarma de volumen {tipo.upper()} de {moneda.upper()} eliminada.")
            else:
                send_message(chat_id, f"⚠️ No había esa alarma activa.")

    elif text.lower() == "/alarmas":
        user_alarmas = [a for a in alarmas if a["chat_id"] == chat_id]
        if not user_alarmas:
            send_message(chat_id, "⛔ No tienes alarmas activas.")
            return
        lineas = []
        for a in user_alarmas:
            if "precio_objetivo" in a:
                lineas.append(f"• PRECIO: {a['moneda'].upper()} a ${a['precio_objetivo']:.2f}")
            elif "tipo_volumen" in a:
                lineas.append(f"• VOLUMEN: {a['moneda'].upper()} {a['tipo_volumen'].upper()} > {a['umbral']}")
            elif "ema_period" in a:
                tol_cfg = a.get("tolerancia", "auto")
                if isinstance(tol_cfg, str) and tol_cfg.lower() == "auto":
                    tol_txt = "auto"
                else:
                    tol_txt = f"±{float(tol_cfg)*100:.4f}%"
                lineas.append(f"• EMA: {a['moneda'].upper()} EMA{a['ema_period']} @ {a.get('intervalo','1h')} ({tol_txt})")
        send_message(chat_id, "🔔 Tus alarmas:\n" + "\n".join(lineas))

    elif text.lower().startswith("/crypto precio"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[2].lower()
            try:
                r = requests.get(CRYPTO_API, params={"name": moneda}, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    precio = data.get("precio")  # la mini-API devuelve "precio"
                    if precio is not None:
                        send_message(chat_id, f"💰 El precio de {moneda.upper()} es: ${precio:.4f} USD")
                    else:
                        send_message(chat_id, f"⚠️ No se encontró el precio de {moneda.upper()}.")
                else:
                    send_message(chat_id, f"❌ No se pudo obtener el precio de '{moneda}'.")
            except Exception as e:
                send_message(chat_id, f"⚠️ Error consultando la API: {e}")
        else:
            send_message(chat_id, "❗ Usa el formato: /crypto precio bitcoin")

def main():
    print("📱 Escuchando mensajes del bot...")
    offset = None
    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            if "message" in update:
                handle_message(update["message"])
        verificar_alarmas_tick()
        time.sleep(2)

# --- Servidor Flask interno (5001) en segundo plano ---
threading.Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    main()
