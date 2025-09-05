# resources/ema_graph_resource.py
import io
import os
import math
import time
import requests
from flask import Response, request, jsonify, current_app
from flask_restful import Resource
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# =========================
#  NUEVO: configuración de guardado
# =========================
# Carpeta base donde guardar imágenes (relativa al proyecto)
DEFAULT_SAVE_DIR = os.environ.get("EMA_SAVE_DIR", os.path.join("static", "ema"))
# Forzar que todos los guardados queden dentro de esta base (anti directory traversal)
ALLOWED_BASE_ABSPATH = os.path.abspath(DEFAULT_SAVE_DIR)

# Cache simple en memoria para mapear baseAsset -> SYMBOL frente a USDT
_symbol_cache = {}

def _build_symbol_cache():
    """Carga símbolos de Binance (solo pares con USDT y en TRADING)."""
    global _symbol_cache
    if _symbol_cache:
        return
    try:
        r = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=12)
        r.raise_for_status()
        data = r.json()
        for s in data.get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                base = s["baseAsset"].lower()   # p.ej. 'doge'
                _symbol_cache[base] = s["symbol"]  # p.ej. 'DOGEUSDT'
    except Exception as e:
        print(f"⚠️ No se pudo construir caché de símbolos: {e}")

def _normalize_par_to_symbol(par: str) -> str:
    """
    Acepta formatos:
      - 'doge', 'DOGE'
      - 'DOGEUSDT'
      - 'DOGE/USDT'
    y devuelve 'DOGEUSDT'. Lanza ValueError si no se puede mapear.
    """
    _build_symbol_cache()
    if not par:
        raise ValueError("Par vacío")

    p = par.strip().upper().replace(" ", "")

    # DOGE/USDT -> DOGEUSDT
    if "/USDT" in p:
        p = p.replace("/USDT", "USDT")

    # Ya es DOGEUSDT
    if p.endswith("USDT") and len(p) > 4:
        return p

    # Es solo base (DOGE)
    base = p.lower()
    sym = _symbol_cache.get(base)
    if sym:
        return sym

    raise ValueError(f"Par no soportado o no encontrado: {par}")

def _validate_interval(intervalo: str) -> str:
    """
    Intervalos admitidos de Binance. Agrega más si quieres.
    """
    allowed = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d"}
    if intervalo not in allowed:
        raise ValueError(f"Intervalo inválido '{intervalo}'. Usa uno de: {', '.join(sorted(allowed))}")
    return intervalo

def _ema(values, period):
    """
    EMA manual (sin pandas). Devuelve lista del mismo largo, con NaN al inicio
    hasta completar el período.
    """
    if period <= 0:
        raise ValueError("Periodo de EMA debe ser > 0")

    k = 2 / (period + 1.0)
    ema_list = []
    ema_prev = None

    for i, v in enumerate(values):
        if v is None:
            ema_list.append(math.nan)
            continue
        if i < period - 1:
            ema_list.append(math.nan)
            continue
        if i == period - 1:
            # SMA para la semilla
            window = [x for x in values[:period] if x is not None]
            if not window or len(window) < period:
                ema_list.append(math.nan)
                ema_prev = None
            else:
                sma = sum(window) / float(period)
                ema_list.append(sma)
                ema_prev = sma
            continue

        # EMA_t = k * Price_t + (1-k) * EMA_{t-1}
        if ema_prev is None:
            ema_list.append(math.nan)
        else:
            ema_t = (v * k) + (ema_prev * (1 - k))
            ema_list.append(ema_t)
            ema_prev = ema_t

    return ema_list

def _fetch_klines(symbol: str, interval: str, limit: int):
    """
    Pide klines a Binance. Devuelve listas: timestamps (ms), open, high, low, close (float)
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()

    ts, open_, high, low, close = [], [], [], [], []
    for k in data:
        # kline format:
        # 0 open time, 1 open, 2 high, 3 low, 4 close, 5 volume, ...
        ts.append(k[0])
        open_.append(float(k[1]))
        high.append(float(k[2]))
        low.append(float(k[3]))
        close.append(float(k[4]))
    return ts, open_, high, low, close

def _safe_join_under_base(base_dir: str, filename: str) -> str:
    """
    Asegura que el path final queda bajo base_dir (mitiga path traversal).
    """
    target_dir_abs = os.path.abspath(base_dir)
    if not target_dir_abs.startswith(ALLOWED_BASE_ABSPATH):
        # si alguien pasó un base_dir fuera de la raíz permitida, forzamos DEFAULT
        target_dir_abs = ALLOWED_BASE_ABSPATH
    os.makedirs(target_dir_abs, exist_ok=True)
    final_abs = os.path.abspath(os.path.join(target_dir_abs, filename))
    if not final_abs.startswith(target_dir_abs):
        # nombre malicioso con .. etc.
        raise ValueError("Nombre de archivo inválido.")
    return final_abs

class EMAGraphResource(Resource):
    """
    GET /ema-graph
      par:       str  (ej: DOGE/USDT, doge)
      intervalo: str  (ej: 1h, 4h, 1d)
      num_velas: int  (ej: 30)
      save:      bool (opcional) -> 'true'|'false' (default 'true')
      save_dir:  str  (opcional) -> subcarpeta bajo static/ema (p. ej. 'doge', 'custom')
      filename:  str  (opcional) -> nombre sin espacios/raros; si no, se autogenera.
      mode:      str  (opcional) -> 'image' (default) | 'json'
    Respuestas:
      - mode=image -> image/png
      - mode=json  -> {"file_path": "...", "file_url": "..."} (y también guarda si save=true)
    """
    def get(self):
        par = request.args.get("par", "DOGE/USDT")
        intervalo = request.args.get("intervalo", "1h")
        num_velas = request.args.get("num_velas", "30")

        # NUEVO: flags de guardado
        save_flag = request.args.get("save", "true").lower() == "true"
        save_dir_param = request.args.get("save_dir")  # puede ser None
        filename_param = request.args.get("filename")  # puede ser None
        mode = request.args.get("mode", "image").lower()

        # Validaciones
        try:
            num_velas = int(num_velas)
            if not (10 <= num_velas <= 1000):
                raise ValueError("num_velas fuera de rango (10–1000).")
        except Exception:
            return jsonify({"error": "num_velas debe ser entero (10–1000)."}), 400

        try:
            intervalo = _validate_interval(intervalo)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        try:
            symbol = _normalize_par_to_symbol(par)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        # Traer velas
        try:
            ts, _o, _h, _l, c = _fetch_klines(symbol, intervalo, num_velas)
        except requests.HTTPError as e:
            return jsonify({"error": f"Error HTTP Binance: {e}"}), 502
        except Exception as e:
            return jsonify({"error": f"No se pudieron obtener klines: {e}"}), 500

        # EMAs
        ema20  = _ema(c, 20)
        ema50  = _ema(c, 50)
        ema100 = _ema(c, 100)
        ema200 = _ema(c, 200)

        # Eje X legible: usar índice + etiqueta última marca con fecha
        x = list(range(len(c)))
        ts_last = time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts[-1]/1000)) if ts else ""

        # Graficar
        fig = plt.figure(figsize=(10, 5), dpi=150)
        plt.plot(x, c, label=f"Cierre {symbol}")
        plt.plot(x, ema20,  label="EMA 20")
        plt.plot(x, ema50,  label="EMA 50")
        plt.plot(x, ema100, label="EMA 100")
        plt.plot(x, ema200, label="EMA 200")

        plt.title(f"{symbol} • {intervalo} • {num_velas} velas  (última: {ts_last} UTC)")
        plt.xlabel("Velas (recientes → derecha)")
        plt.ylabel("Precio")
        plt.grid(True, alpha=0.3)
        plt.legend(loc="best")
        plt.tight_layout()

        # =========================
        #  NUEVO: Guardar al disco
        # =========================
        # carpeta final (dentro de DEFAULT_SAVE_DIR si no se pasa nada)
        if save_dir_param:
            target_dir = os.path.join(DEFAULT_SAVE_DIR, save_dir_param)
        else:
            # subcarpeta por símbolo (ej. static/ema/DOGEUSDT)
            target_dir = os.path.join(DEFAULT_SAVE_DIR, symbol)

        # nombre de archivo
        if filename_param:
            # sanitizar: quitar espacios y caracteres raros
            safe_name = "".join(ch for ch in filename_param if ch.isalnum() or ch in ("-", "_", "."))
            if not safe_name.endswith(".png"):
                safe_name += ".png"
            fname = safe_name
        else:
            ts_now = int(time.time())
            fname = f"{symbol}_{intervalo}_{num_velas}_{ts_now}.png"

        file_abs = None
        file_url = None

        if save_flag:
            try:
                file_abs = _safe_join_under_base(target_dir, fname)
                # asegurar carpeta
                os.makedirs(os.path.dirname(file_abs), exist_ok=True)
                # guardar PNG en disco
                plt.savefig(file_abs, format="png")
                # construir URL pública si está bajo /static
                # Flask sirve '/static' por defecto desde ./static
                # Si DEFAULT_SAVE_DIR apunta a 'static/ema', file_url quedará en /static/ema/...
                proj_root = os.path.abspath(".")
                static_abs = os.path.abspath(os.path.join(proj_root, "static"))
                if os.path.abspath(file_abs).startswith(static_abs):
                    rel_path = os.path.relpath(file_abs, static_abs).replace("\\", "/")
                    file_url = f"/static/{rel_path}"
            except Exception as e:
                plt.close(fig)
                return jsonify({"error": f"No se pudo guardar la imagen: {e}"}), 500

        # Generar PNG en memoria (para respuesta image/png)
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        # Si piden JSON, devolvemos paths/urls
        if mode == "json":
            return jsonify({
                "symbol": symbol,
                "intervalo": intervalo,
                "num_velas": num_velas,
                "saved": bool(save_flag),
                "file_path": file_abs,   # ruta absoluta en el servidor (si save=true)
                "file_url": file_url     # URL relativa para consumir vía HTTP (si está bajo /static)
            })

        # Por defecto, devolvemos la imagen
        return Response(buf.getvalue(), mimetype="image/png")
