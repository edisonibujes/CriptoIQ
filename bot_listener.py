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

def obtener_analisis(moneda, dias):
    valid_ids_url = "https://api.coingecko.com/api/v3/coins/list"
    try:
        ids_response = requests.get(valid_ids_url)
        ids_data = ids_response.json()
        ids_disponibles = [item['id'] for item in ids_data]
        if moneda not in ids_disponibles:
            sugerencias = [id for id in ids_disponibles if moneda in id][:5]
            sugerencia_texto = ("\n👉 ¿Quizás quisiste decir?:\n" + "\n".join(sugerencias)) if sugerencias else ""
            return f"❌ '{moneda}' no es un ID válido en CoinGecko.{sugerencia_texto}"
    except Exception as e:
        print(f"⚠️ Error al verificar ID de moneda: {e}")

    url = f"https://api.coingecko.com/api/v3/coins/{moneda}/market_chart"
    params = {"vs_currency": "usd", "days": dias}
    response = requests.get(url, params=params)

    if response.status_code != 200:
        return f"❌ Error: no se pudo consultar {moneda}. Verifica el nombre."

    data = response.json()
    precios_brutos = data.get("prices", [])
    precios = [p[1] for p in precios_brutos if isinstance(p, list) and len(p) == 2 and isinstance(p[1], (int, float)) and p[1] > 0]

    if not precios:
        return f"⚠️ No se encontraron precios válidos para {moneda} en los últimos {dias} días."

    max_price = max(precios)
    min_price = min(precios)
    current_price = precios[-1]

    diff_max = ((max_price - current_price) / max_price) * 100
    diff_min = ((current_price - min_price) / min_price) * 100

    mensaje = (
        f"📊 Análisis de {moneda.upper()} (últimos {dias} días):\n"
        f"💰 Precio actual: ${current_price:.2f}\n"
        f"📈 Máximo: ${max_price:.2f} ({diff_max:.2f}% por debajo)\n"
        f"📉 Mínimo: ${min_price:.2f} ({diff_min:.2f}% por encima)"
    )
    return mensaje

def verificar_alarmas_tick():
    for alarma in alarmas[:]:
        moneda = alarma["moneda"]
        chat_id = alarma["chat_id"]
        precio_objetivo = alarma["precio_objetivo"]
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": moneda, "vs_currencies": "usd"}
            r = requests.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                precio_actual = data.get(moneda, {}).get("usd", 0)
                if precio_actual >= precio_objetivo:
                    send_message(chat_id, f"🚨 ¡{moneda.upper()} ha alcanzado ${precio_actual:.2f}!")
                    alarmas.remove(alarma)
                    guardar_alarmas()
        except Exception as e:
            print(f"⚠️ Error verificando alarma: {e}")

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
            "/crypto precio <moneda> — Precio actual desde API externa."
        )
        send_message(chat_id, ayuda)

    elif text.lower() == "/alarmas":
        user_alarmas = [a for a in alarmas if a["chat_id"] == chat_id]
        if user_alarmas:
            mensaje = "🔔 Tus alarmas:\n" + "\n".join([
                f"• {a['moneda'].upper()} a ${a['precio_objetivo']:.2f}" for a in user_alarmas
            ])
        else:
            mensaje = "⛔ No tienes alarmas activas."
        send_message(chat_id, mensaje)

    elif text.lower().startswith("/alarma"):
        partes = text.strip().split()
        if len(partes) == 3:
            moneda = partes[1].lower()
            valid_ids_url = "https://api.coingecko.com/api/v3/coins/list"
            try:
                ids_response = requests.get(valid_ids_url)
                ids_data = ids_response.json()
                ids_disponibles = [item['id'] for item in ids_data]
                if moneda not in ids_disponibles:
                    sugerencias = [id for id in ids_disponibles if moneda in id][:5]
                    sugerencia_texto = ("\n👉 ¿Quizás quisiste decir?:\n" + "\n".join(sugerencias)) if sugerencias else ""
                    send_message(chat_id, f"❌ '{moneda}' no es un ID válido en CoinGecko.{sugerencia_texto}")
                    return
            except Exception as e:
                print(f"⚠️ Error al verificar ID de moneda: {e}")
            try:
                precio_objetivo = float(partes[2])
                actualizada = False
                for a in alarmas:
                    if a["chat_id"] == chat_id and a["moneda"] == moneda:
                        a["precio_objetivo"] = precio_objetivo
                        actualizada = True
                        break
                if not actualizada:
                    alarma = {
                        "chat_id": chat_id,
                        "moneda": moneda,
                        "precio_objetivo": precio_objetivo
                    }
                    alarmas.append(alarma)
                guardar_alarmas()
                estado = "🔄 Alarma actualizada" if actualizada else "⏰ Alarma configurada"
                send_message(chat_id, f"{estado} para {moneda.upper()} a ${precio_objetivo:.2f}")
            except ValueError:
                send_message(chat_id, "⚠️ El precio debe ser numérico. Ej: /alarma bitcoin 30000")
        else:
            send_message(chat_id, "❗ Usa el formato: /alarma bitcoin 30000")


    elif text.lower().startswith("/alarmas"):
        user_alarmas = [a for a in alarmas if a["chat_id"] == chat_id]
        if user_alarmas:
            mensaje = "🔔 Tus alarmas:\n" + "\n".join([
                f"• {a['moneda'].upper()} a ${a['precio_objetivo']:.2f}" for a in user_alarmas
            ])
        else:
            mensaje = "⛔ No tienes alarmas activas."
        send_message(chat_id, mensaje)

    elif text.lower().startswith("/eliminaralarma"):
        partes = text.split()
        if len(partes) == 2:
            moneda = partes[1].lower()
            original = len(alarmas)
            alarmas[:] = [a for a in alarmas if not (a["chat_id"] == chat_id and a["moneda"] == moneda)]
            if len(alarmas) < original:
                guardar_alarmas()
                send_message(chat_id, f"🗑️ Alarma de {moneda.upper()} eliminada.")
            else:
                send_message(chat_id, f"⚠️ No se encontró alarma para {moneda.upper()}.")
        else:
            send_message(chat_id, "❗ Usa el formato: /eliminaralarma bitcoin")

    elif text.lower().startswith("/crypto precio"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[2].lower()
            try:
                r = requests.get(CRYPTO_API, params={"name": moneda})
                if r.status_code == 200:
                    data = r.json()
                    precio = data.get("precio_usd")
                    if precio is None:
                        msg = f"⚠️ No se pudo leer el precio de {moneda.upper()}."
                    else:
                        msg = f"💰 El precio de {moneda.upper()} es: ${precio:.2f} USD"
                else:
                    msg = f"❌ No se pudo obtener el precio de '{moneda}'."
            except Exception as e:
                msg = f"⚠️ Error consultando la API: {e}"
            send_message(chat_id, msg)
        else:
            send_message(chat_id, "❗ Usa el formato: /crypto precio bitcoin")

if __name__ == "__main__":
    main()
