import requests
import time

print("🔥 Iniciando bot_listener")  # 👈 Este es solo para confirmar ejecución
BOT_TOKEN = "7674766197:AAFB6QlNXkspIXejhmz8rCJGM-yDAm9I5u8"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTO_API = "http://127.0.0.1:5000/crypto-data-actual"

def get_updates(offset=None):
    response = requests.get(f"{API_URL}/getUpdates", params={"offset": offset})
    return response.json()

def send_message(chat_id, text):
    url = f"{API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

def handle_message(message):
    print("📨 Mensaje recibido:", message)  # 👀 Debug
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.lower().startswith("/crypto precio"):
        partes = text.split()
        if len(partes) == 3:
            moneda = partes[2].lower()
            try:
                r = requests.get(CRYPTO_API, params={"name": moneda})
                if r.status_code == 200:
                    data = r.json()
                    precio = data.get("precio_usd")
                    msg = f"💰 El precio de {moneda.upper()} es: ${precio:.2f} USD"
                else:
                    msg = f"❌ No se pudo obtener el precio de '{moneda}'."
            except Exception as e:
                msg = f"⚠️ Error consultando la API: {e}"
            send_message(chat_id, msg)
        else:
            send_message(chat_id, "❗ Usa el formato: /crypto precio bitcoin")

def main():
    print("📡 Escuchando mensajes del bot...")
    offset = None
    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            handle_message(update["message"])
        time.sleep(1)

if __name__ == "__main__":
    main()

