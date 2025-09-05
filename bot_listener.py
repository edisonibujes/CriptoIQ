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
BOT_TOKEN = "7674766197:AAEwF84b6WR40XWpbilzo5DpzgowKk1K454"  # <-- PON AQUÍ TU TOKEN DE TELEGRAM
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mini-API interna del bot (NO chocar con application.py en 5000)
LOCAL_FLASK_PORT = 5001
CRYPTO_API = f"http://127.0.0.1:{LOCAL_FLASK_PORT}/crypto-data-actual"

# Tu application.py imprimió prefijo VACÍO => deja cadena vacía
# Si algún día imprime "/api", cambia a: API_PREFIX = "/api"
API_PREFIX = ""
API_SERVER_PORT = 5000
ALARM_FILE = "alarmas.json"


# =========================
# Utilidades de alarmas
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

def verificar_alarmas_tick():
    # Alarmas de precio (CoinGecko)
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

    # Alarmas de volumen (verde/rojo) usando mini-API interna
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
# Lógica del Bot
# =========================
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text.lower().startswith("/ayuda"):
        ayuda = (
            "📘 Comandos disponibles:\n"
            "/precio <moneda> <días> — Análisis histórico (Coingecko) + volumen 1h (Binance).\n"
            "/alarma <moneda> <precio> — Alerta si el precio sube hasta el objetivo.\n"
            "/alarmas — Lista tus alarmas.\n"
            "/eliminaralarma <moneda> — Borra una alarma de precio.\n"
            "/alarma volumen <moneda> <verde|rojo> <umbral> — Alerta de volumen personalizada.\n"
            "/eliminarvolumen <moneda> <verde|rojo> — Elimina una alarma de volumen.\n"
            "/EMA <moneda> <intervalo> <velas> — Gráfica con EMAs 20, 50, 100 y 200.\n"
            "   Ej: /EMA doge 1h 50"
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

                # Intento 1: pedir JSON para usar file_url (imagen guardada en static/ema/telegram/)
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
        if len(partes) == 2:
            moneda = partes[1].lower()
            prev = len(alarmas)
            alarmas[:] = [a for a in alarmas if not (a["chat_id"] == chat_id and a.get("moneda") == moneda and "precio_objetivo" in a)]
            if len(alarmas) < prev:
                guardar_alarmas()
                send_message(chat_id, f"🗑️ Alarma de {moneda.upper()} eliminada.")
            else:
                send_message(chat_id, f"⚠️ No había alarma de {moneda.upper()}.")

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
        mensaje = "🔔 Tus alarmas:\n" + "\n".join(
            f"• {a['moneda'].upper()} a ${a['precio_objetivo']:.2f}" if "precio_objetivo" in a else
            f"• Volumen {a['tipo_volumen'].upper()} de {a['moneda'].upper()} > {a['umbral']}"
            for a in user_alarmas
        )
        send_message(chat_id, mensaje)

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
