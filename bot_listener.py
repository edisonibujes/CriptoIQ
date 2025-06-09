
import requests
from flask import Flask, request, jsonify

BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_symbol_cache = {}

def _build_symbol_cache():
    global _symbol_cache
    try:
        r = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        for s in data.get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                base = s["baseAsset"].lower()
                _symbol_cache[base] = s["symbol"]
    except Exception as e:
        print(f"⚠️ No se pudo construir caché de símbolos: {e}")

def run_flask():
    app = Flask(__name__)
    _build_symbol_cache()

    @app.route('/crypto-data-actual')
    def crypto_data():
        name = request.args.get("name", "bitcoin").lower()

        # Alias comunes → símbolos
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
            params = {
                "symbol": symbol,
                "interval": "1h",
                "limit": 10
            }
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()

            volumen_rojo = 0
            volumen_verde = 0
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

    app.run(host="127.0.0.1", port=5000)


import requests
import time
import json
import os

print("🔥 Iniciando bot_listener")
BOT_TOKEN = "7674766197:AAFB6QlNXkspIXejhmz8rCJGM-yDAm9I5u8"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTO_API = "http://127.0.0.1:5000/crypto-data-actual"
ALARM_FILE = "alarmas.json"

if os.path.exists(ALARM_FILE):
    with open(ALARM_FILE, "r") as f:
        alarmas = json.load(f)
else:
    alarmas = []

def guardar_alarmas():
    with open(ALARM_FILE, "w") as f:
        json.dump(alarmas, f)

def get_updates(offset=None):
    response = requests.get(f"{API_URL}/getUpdates", params={"offset": offset})
    return response.json()

def send_message(chat_id, text):
    print(f"✉️ Enviando mensaje a {chat_id}: {text}")
    url = f"{API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(url, json=payload)
    print(f"📡 Respuesta de Telegram: {response.status_code} - {response.text}")

def verificar_alarmas_tick():
    for alarma in alarmas[:]:
        if "precio_objetivo" in alarma:
            moneda = alarma["moneda"]
            chat_id = alarma["chat_id"]
            try:
                r = requests.get(f"https://api.coingecko.com/api/v3/simple/price", params={"ids": moneda, "vs_currencies": "usd"})
                if r.status_code == 200:
                    data = r.json()
                    precio_actual = data.get(moneda, {}).get("usd", 0)
                    if precio_actual >= alarma["precio_objetivo"]:
                        send_message(chat_id, f"🚨 ¡{moneda.upper()} ha alcanzado ${precio_actual:.2f}!")
                        alarmas.remove(alarma)
                        guardar_alarmas()
            except Exception as e:
                print(f"⚠️ Error verificando alarma de precio: {e}")

        if "tipo_volumen" in alarma:
            moneda = alarma["moneda"]
            chat_id = alarma["chat_id"]
            tipo = alarma["tipo_volumen"]
            umbral = alarma.get("umbral", 30000 if tipo == "verde" else 60000)
            try:
                r = requests.get(CRYPTO_API, params={"name": moneda})
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

def main():
    print("📱 Escuchando mensajes del bot...")
    offset = None
    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            handle_message(update["message"])
        verificar_alarmas_tick()
        time.sleep(15)

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text.lower().startswith("/ayuda"):
        ayuda = (
            "📘 Comandos disponibles:\n"
            "/precio <moneda> <días> — Análisis histórico.\n"
            "/alarma <moneda> <precio> — Alerta si el precio sube.\n"
            "/alarmas — Lista tus alarmas.\n"
            "/eliminaralarma <moneda> — Borra una alarma.\n"
            "/crypto precio <moneda> — Precio actual desde API externa.\n"
            "/monedas — Lista algunas monedas compatibles con el bot.\n"
            "/alarma volumen <moneda> <verde|rojo> <umbral> — Alerta de volumen personalizada.\n"
            "/eliminarvolumen <moneda> <verde|rojo> — Elimina una alarma de volumen."
        )
        send_message(chat_id, ayuda)


    elif text.lower().startswith("/precio"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[1].lower()
            try:
                dias = int(partes[2])
                url = f"https://api.coingecko.com/api/v3/coins/{moneda}/market_chart"
                params = {"vs_currency": "usd", "days": dias}
                r = requests.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    precios = [p[1] for p in data.get("prices", []) if isinstance(p, list) and len(p) == 2]
                    if precios:
                        max_price = max(precios)
                        min_price = min(precios)
                        actual = precios[-1]
                        msg = (
                            f"📊 {moneda.upper()} últimos {dias} días:\n"
                            f"💰 Actual: ${actual:.2f}\n"
                            f"📈 Máximo: ${max_price:.2f}\n"
                            f"📉 Mínimo: ${min_price:.2f}"
                        )
                    else:
                        msg = "⚠️ No se encontraron datos válidos."
                else:
                    msg = "❌ Error consultando precios."
            except Exception as e:
                msg = f"⚠️ Error al obtener precios: {e}"
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
                r = requests.get(CRYPTO_API, params={"name": moneda})
                if r.status_code == 200:
                    data = r.json()
                    precio = data.get("precio_usd")
                    if precio is not None:
                        send_message(chat_id, f"💰 El precio de {moneda.upper()} es: ${precio:.2f} USD")
                    else:
                        send_message(chat_id, f"⚠️ No se encontró el precio de {moneda.upper()}.")
                else:
                    send_message(chat_id, f"❌ No se pudo obtener el precio de '{moneda}'.")
            except Exception as e:
                send_message(chat_id, f"⚠️ Error consultando la API: {e}")
        else:
            send_message(chat_id, "❗ Usa el formato: /crypto precio bitcoin")

    elif text.lower() == "/monedas":
        try:
            ids_url = "https://api.coingecko.com/api/v3/coins/list"
            response = requests.get(ids_url)
            data = response.json()
            nombres = [f"{item['id']} ({item['symbol'].upper()})" for item in data[:100]]
            mensaje = "🪙 Algunas monedas compatibles:\n" + "\n".join(nombres)
            mensaje += "\n\n🔎 Puedes consultar más monedas en: https://www.coingecko.com/es"
        except Exception as e:
            mensaje = f"⚠️ Error al obtener lista de monedas: {e}"
        send_message(chat_id, mensaje)

import threading

# --- Servidor Flask corriendo en segundo plano ---
threading.Thread(target=run_flask, daemon=True).start()


if __name__ == "__main__":
    main()
