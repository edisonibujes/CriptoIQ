import re

# =========================
# RSI(14) + detección de divergencias (alcista / bajista) + render
# =========================

# =========================
# Yahoo OHLC + EMAs + Matplotlib chart (core fetch) con retries + caché
# =========================
import os, json, time, random

def _yahoo_cache_path(ticker: str, interval: str, range_: str) -> str:
    safe = f"{ticker}_{interval}_{range_}".replace("/", "_").replace(":", "_").replace("!", "1")
    return f"/tmp/yahoo_{safe}.json"

def _yahoo_chart_query(ticker: str, interval: str="60m", range_: str="1mo"):
    """
    OHLC desde Yahoo Finance v8 chart API con:
    - Retries exponenciales (429 friendly)
    - Rotación de host (query1, query2)
    - User-Agent aleatorio
    - Caché local (TTL 15 min) para fallback si hay 429
    """
    endpoints = [
        "https://query1.finance.yahoo.com/v8/finance/chart/",
        "https://query2.finance.yahoo.com/v8/finance/chart/",
    ]
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16 Safari/605.1.15",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    ]
    headers = {"User-Agent": random.choice(user_agents), "Accept": "application/json"}

    params = {"interval": interval, "range": range_}
    cache_file = _yahoo_cache_path(ticker, interval, range_)
    now = time.time()

    # Si hay caché reciente y estamos bajo presión, úsala primero
    if os.path.exists(cache_file):
        try:
            stat = os.stat(cache_file)
            if now - stat.st_mtime <= 15 * 60:  # 15 minutos
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached and cached.get("t") and cached.get("close"):
                    return cached
        except Exception:
            pass

    last_err = None
    # Hasta 5 reintentos con backoff exponencial
    for attempt in range(5):
        base = random.choice(endpoints)
        url = f"{base}{ticker}"
        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 200:
                js = r.json()
                ch = js.get("chart", {})
                if ch.get("error"):
                    last_err = ch["error"]
                    # no sirve insistir si Yahoo devuelve error estructural
                    break
                res_arr = ch.get("result", [])
                if not res_arr:
                    last_err = "empty result"
                    # intenta otro host en siguiente retry
                else:
                    res = res_arr[0]
                    timestamps = res.get("timestamp") or []
                    inds = res.get("indicators", {})
                    quote = (inds.get("quote") or [{}])[0]
                    close = quote.get("close") or []
                    open_ = quote.get("open") or []
                    high  = quote.get("high") or []
                    low   = quote.get("low") or []

                    n = min(len(timestamps), len(close))
                    if n == 0:
                        last_err = "no points"
                    else:
                        t = timestamps[:n]; c = close[:n]; o = open_[:n]; h = high[:n]; l = low[:n]
                        t_clean, o_clean, h_clean, l_clean, c_clean = [], [], [], [], []
                        for i in range(n):
                            if t[i] is None or c[i] is None:
                                continue
                            t_clean.append(t[i])
                            c_clean.append(float(c[i]))
                            o_clean.append(float(o[i]) if i < len(o) and o[i] is not None else None)
                            h_clean.append(float(h[i]) if i < len(h) and h[i] is not None else None)
                            l_clean.append(float(l[i]) if i < len(l) and l[i] is not None else None)

                        if t_clean and c_clean:
                            data = {"t": t_clean, "open": o_clean, "high": h_clean, "low": l_clean, "close": c_clean}
                            # guardar caché
                            try:
                                with open(cache_file, "w", encoding="utf-8") as f:
                                    json.dump(data, f)
                            except Exception:
                                pass
                            return data
                        else:
                            last_err = "no clean data"

            elif r.status_code == 429:
                last_err = "HTTP 429"
                # backoff más largo si es 429
                time.sleep(1.5 * (2 ** attempt))
            else:
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.4 * (2 ** attempt))

        except Exception as e:
            last_err = str(e)
            time.sleep(0.4 * (2 ** attempt))

    # Fallback final: si hay caché aunque sea más vieja, úsala
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached and cached.get("t") and cached.get("close"):
                return cached
        except Exception:
            pass

    print(f"⚠️ Yahoo chart fetch failed: {last_err}")
    return None


def _rsi(values, period: int = 14):
    """RSI estilo Wilder sin pandas. Devuelve lista de floats (None si no hay suficientes datos)."""
    if not values or len(values) < period + 1:
        return [None] * len(values)
    rsi = [None] * len(values)
    gains = []
    losses = []
    for i in range(1, period+1):
        delta = values[i] - values[i-1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = (avg_gain / avg_loss) if avg_loss != 0 else float('inf')
    rsi[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period+1, len(values)):
        delta = values[i] - values[i-1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = (avg_gain / avg_loss) if avg_loss != 0 else float('inf')
        rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def _find_swings(arr, window=3, find_high=True):
    idxs = []
    n = len(arr)
    for i in range(window, n - window):
        mid = arr[i]
        if mid is None:
            continue
        ok = True
        for j in range(1, window+1):
            a = arr[i-j]; b = arr[i+j]
            if a is None or b is None:
                ok = False; break
            if find_high:
                if not (mid > a and mid > b):
                    ok = False; break
            else:
                if not (mid < a and mid < b):
                    ok = False; break
        if ok:
            idxs.append(i)
    return idxs

def _detect_divergences(close, rsi_vals, timestamps, window=3):
    highs_p = _find_swings(close, window=window, find_high=True)
    lows_p  = _find_swings(close, window=window, find_high=False)
    highs_r = _find_swings(rsi_vals, window=window, find_high=True)
    lows_r  = _find_swings(rsi_vals, window=window, find_high=False)

    last = {"bullish": None, "bearish": None}

    def nearest(idx_list, i, tol=10):
        return [j for j in idx_list if abs(j - i) <= tol]

    for i2 in reversed(highs_p):
        cands = nearest(highs_p, i2, 60)
        for i1 in reversed(cands):
            if i1 >= i2: continue
            if close[i2] is None or close[i1] is None: continue
            if close[i2] <= close[i1]: continue
            r2c = nearest(highs_r, i2, 60)
            r1c = nearest(highs_r, i1, 60)
            if not r2c or not r1c: continue
            r2 = r2c[-1]; r1 = r1c[-1]
            if rsi_vals[r2] is None or rsi_vals[r1] is None: continue
            if rsi_vals[r2] < rsi_vals[r1]:
                last["bearish"] = (i2, timestamps[i2]); break
        if last["bearish"]: break

    for i2 in reversed(lows_p):
        cands = nearest(lows_p, i2, 60)
        for i1 in reversed(cands):
            if i1 >= i2: continue
            if close[i2] is None or close[i1] is None: continue
            if close[i2] >= close[i1]: continue
            r2c = nearest(lows_r, i2, 60)
            r1c = nearest(lows_r, i1, 60)
            if not r2c or not r1c: continue
            r2 = r2c[-1]; r1 = r1c[-1]
            if rsi_vals[r2] is None or rsi_vals[r1] is None: continue
            if rsi_vals[r2] > rsi_vals[r1]:
                last["bullish"] = (i2, timestamps[i2]); break
        if last["bullish"]: break
    return last

def generate_rsi_divergence_chart(symbol: str, out_dir="/tmp", interval="60m", range_="1mo"):
    yahoo = _tv_symbol_to_yahoo(symbol) or symbol
    data = _yahoo_chart_query(yahoo, interval=interval, range_=range_)
    if not data or not data.get("close"):
        raise RuntimeError("No se pudo obtener OHLC de Yahoo.")
    close = [float(x) if x is not None else None for x in data["close"]]
    ts = data["t"]
    rsi_vals = _rsi(close, 14)

    divs = _detect_divergences(close, rsi_vals, ts, window=3)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import datetime as _dt
    xs = [_dt.datetime.utcfromtimestamp(t) for t in ts]

    fig = plt.figure(figsize=(12, 7))
    ax1 = fig.add_subplot(2,1,1)
    ax2 = fig.add_subplot(2,1,2, sharex=ax1)
    ax1.plot(xs, close, label="Close"); ax1.grid(True, alpha=0.25)
    ax1.set_title(f"{symbol} ({yahoo}) — Precio")
    ax2.plot(xs, rsi_vals, label="RSI(14)"); ax2.axhline(70, linestyle="--", alpha=0.6); ax2.axhline(30, linestyle="--", alpha=0.6)
    ax2.set_title("RSI(14)"); ax2.grid(True, alpha=0.25)

    if divs.get("bearish"):
        i, _ = divs["bearish"]; ax1.plot(xs[i], close[i], "v", markersize=10); ax2.plot(xs[i], rsi_vals[i], "v", markersize=10)
        ax1.annotate("Divergencia Bajista", (xs[i], close[i]), xytext=(10,10), textcoords="offset points")
    if divs.get("bullish"):
        i, _ = divs["bullish"]; ax1.plot(xs[i], close[i], "^", markersize=10); ax2.plot(xs[i], rsi_vals[i], "^", markersize=10)
        ax1.annotate("Divergencia Alcista", (xs[i], close[i]), xytext=(10,-15), textcoords="offset points")

    plt.tight_layout()
    outfile = f"{out_dir.rstrip('/')}/rsi_div_{symbol.replace(':','_').replace('!','1')}.png"
    plt.savefig(outfile, dpi=140); plt.close(fig)

    last_bull = divs.get("bullish")[1] if divs.get("bullish") else None
    last_bear = divs.get("bearish")[1] if divs.get("bearish") else None
    return outfile, last_bull, last_bear
# bot_listener.py
import os
import json
import time
import threading
import requests

print("🔥 Iniciando bot_listener")

# =========================

def _inject_ema_pine(page):
    """
    Intenta abrir el editor Pine y pegar un script con EMAs 20/50/100/200.
    Es best-effort: si falla, no lanza excepción (el gráfico igual se captura).
    """
    PINE_CODE = """//@version=5
indicator("EMAs 20/50/100/200", overlay=true)
ema20  = ta.ema(close, 20)
ema50  = ta.ema(close, 50)
ema100 = ta.ema(close, 100)
ema200 = ta.ema(close, 200)
plot(ema20,  title="EMA20")
plot(ema50,  title="EMA50")
plot(ema100, title="EMA100")
plot(ema200, title="EMA200")
"""
    try:
        # Abrir pestaña de Pine Editor si existe
        try:
            tab = page.get_by_role("tab", name=re.compile("Pine Editor|Pine Script", re.I))
            tab.click(timeout=3000)
        except Exception:
            # Fallback: botón con texto
            try:
                page.get_by_text("Pine Editor", exact=False).first.click(timeout=2000)
            except Exception:
                pass

        # Intentar enfocar el editor y pegar el script
        # Algunos builds tienen un textarea oculto; probamos con varios selectores frecuentes
        editor = None
        for sel in [
            'div.monaco-editor',         # Monaco editor
            'div.js-code-editor',        # Legacy editor
            'div.tv-pine-editor',        # Pine editor wrapper
            'textarea',                  # raw textarea (rare)
        ]:
            try:
                cand = page.locator(sel).first
                if cand and cand.is_visible():
                    cand.click(timeout=1500)
                    editor = cand
                    break
            except Exception:
                continue

        # Si no logramos identificar el editor, probamos a mandar atajos
        if editor is None:
            try:
                page.keyboard.press("Control+A")
            except Exception:
                pass

        # Insertar texto del script
        try:
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(PINE_CODE)
        except Exception:
            # Fallback lento con type()
            try:
                page.keyboard.press("Control+A")
                page.keyboard.type(PINE_CODE, delay=2)
            except Exception:
                pass

        # Compilar/Añadir al gráfico (Ctrl+Enter o botón 'Añadir al gráfico')
        ok = False
        try:
            page.keyboard.press("Control+Enter")
            ok = True
        except Exception:
            pass
        if not ok:
            for label in ["Añadir al gráfico", "Add to chart"]:
                try:
                    page.get_by_role("button", name=label, exact=False).click(timeout=2000)
                    ok = True
                    break
                except Exception:
                    continue
    except Exception:
        # No interrumpimos el flujo
        pass
# TradingView helpers (screenshot) + Yahoo price for futures
# =========================
from urllib.parse import quote
  # asegúrate de tenerlo importado arriba

def _tv_symbol_to_yahoo(tv_symbol: str) -> str | None:
    """
    Convierte símbolos de TradingView tipo 'MES1!' a Yahoo:
      MES1! -> MES=F,  ES1! -> ES=F,  NQ1! -> NQ=F,  YM1! -> YM=F,  RTY1! -> RTY=F, etc.
    Si llega 'MES!' (sin dígito), también mapea a MES=F.
    Para cripto/acciones (BINANCE:BTCUSDT, AAPL, etc.) deja pasar o convierte si corresponde.
    """
    if not tv_symbol:
        return None

    s = tv_symbol.strip().upper()
    # Quita exchange prefix tipo CME_MINI:, BINANCE:, etc. pero conserva cripto si lo necesitas
    if ":" in s:
        ex, sym = s.split(":", 1)
        s = sym.strip()

    # Futuros más comunes → mapeo directo por raíz
    root_map = {
        "ES": "ES=F",   # E-mini S&P 500
        "MES": "MES=F", # Micro E-mini S&P 500
        "NQ": "NQ=F",   # E-mini Nasdaq 100
        "MNQ": "MNQ=F", # Micro E-mini Nasdaq 100
        "YM": "YM=F",   # E-mini Dow
        "MYM": "MYM=F", # Micro E-mini Dow
        "RTY": "RTY=F", # E-mini Russell 2000
        "M2K": "M2K=F", # Micro Russell 2000
        "CL": "CL=F",   # Crude Oil WTI
        "GC": "GC=F",   # Gold
        "SI": "SI=F",   # Silver
        "HG": "HG=F",   # Copper
        "ZS": "ZS=F",   # Soybeans
        "ZC": "ZC=F",   # Corn
        "ZW": "ZW=F",   # Wheat
    }

    # Patrones aceptados: MES1! / MES! / ES1! / ES!
    m = re.match(r"^([A-Z]+)(\d+)?!$", s)
    if m:
        root = m.group(1)
        if root in root_map:
            return root_map[root]
        # Fallback genérico: raíz + '=F'
        return f"{root}=F"

    # Si no coincide el patrón con !, intenta raíces conocidas sin '!' (por si ya viene limpio)
    for r, yy in root_map.items():
        if s.startswith(r):
            return yy

    # No es futuro conocido; para acciones tipo AAPL o ETFs tipo SPY, devuélvelo tal cual
    return s

def _tv_key(symbol: str) -> str:
    """
    Llave canónica para alarmas TV: usamos el símbolo Yahoo (ej: MES=F).
    Así MES1! y MES! terminan apuntando a la misma llave y no se duplican.
    """
    ysym = _tv_symbol_to_yahoo(symbol) or symbol.upper()
    return ysym


# --- Precio Yahoo robusto con retries, rotación y fallback ---
_yahoo_price_cache = {}  # {ticker: (ts, price)}

import time, random

def _sleep_with_jitter(base_delay: float, attempt: int):
    """Pequeña pausa con jitter exponencial para backoff de reintentos."""
    delay = base_delay * (1.2 ** attempt) + random.uniform(0, 0.3)
    time.sleep(delay)


def _yahoo_quote_price(ticker: str, ttl_sec: int = 15) -> float | None:
    """Precio desde v7/finance/quote con retries, rotación query1/query2, headers y caché 15s. Fallback al último close chart/spark."""
    import time, random, requests

    now = time.time()
    hit = _yahoo_price_cache.get(ticker)
    if hit and (now - hit[0]) <= ttl_sec:
        return hit[1]

    endpoints = [
        "https://query1.finance.yahoo.com/v7/finance/quote",
        "https://query2.finance.yahoo.com/v7/finance/quote",
    ]
    headers = {
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16 Safari/605.1.15",
        ]),
    }
    params = {"symbols": ticker}
    last_err = None

    for attempt in range(5):
        base = random.choice(endpoints)
        try:
            r = requests.get(base, params=params, headers=headers, timeout=25)
            if r.status_code == 200:
                js = r.json()
                res = (js.get("quoteResponse") or {}).get("result") or []
                if res:
                    q = res[0]
                    price = (
                        q.get("regularMarketPrice") or
                        q.get("postMarketPrice") or
                        q.get("preMarketPrice") or
                        q.get("regularMarketPreviousClose")
                    )
                    if price is not None:
                        price = float(price)
                        _yahoo_price_cache[ticker] = (now, price)
                        return price
                last_err = "empty result"
            elif r.status_code == 429:
                _sleep_with_jitter(1.2, attempt)
                last_err = "HTTP 429"
                continue
            else:
                _sleep_with_jitter(0.5, attempt)
                last_err = f"HTTP {r.status_code}"
                continue
        except Exception as e:
            _sleep_with_jitter(0.6, attempt)
            last_err = str(e)
            continue

    # Fallback: último close de chart/spark
    data = _yahoo_chart_query(ticker, interval="60m", range_="1mo")
    if data and data.get("close"):
        price = float(data["close"][-1])
        _yahoo_price_cache[ticker] = (now, price)
        return price

    print(f"⚠️ Yahoo price error for {ticker}: {last_err}")
    return None

def obtener_precio(symbol: str) -> float | None:
    """Wrapper: mapea TradingView → Yahoo y obtiene precio robusto."""
    ysym = _tv_symbol_to_yahoo(symbol) or symbol
    return _yahoo_quote_price(ysym)


def _tv_url_for(symbol: str) -> str:
    """
    Construye URL pública de TradingView.
    - Usa el layout público configurado en env var TV_PUBLIC_LAYOUT_ID.
    - Si no está configurado, hace fallback a /chart/?symbol=... (sin ID).
    - Mantiene el símbolo con intercambio si no se pasó, por defecto CME_MINI.
    """
    import os
    from urllib.parse import quote

    s = symbol.strip()
    if ":" not in s:
        s = f"CME_MINI:{s}"

    public_id = os.environ.get("TV_PUBLIC_LAYOUT_ID", "").strip()
    base = "https://es.tradingview.com/chart"
    if public_id and public_id.upper() != "TU_LAYOUT_PUBLICO":
        # ID válido publicado desde TradingView (debe incluir las EMAs en el layout)
        return f"{base}/{public_id}/?symbol={quote(s, safe=':!')}"
    # Fallback: sin layout id (cargará layout por defecto del visitante)
    return f"{base}/?symbol={quote(s, safe=':!')}"


def get_tradingview_screenshot(symbol: str, out_dir: str = "/tmp") -> str:
    """
    Abre TradingView (link público) y toma una captura del gráfico.
    - Siempre intenta inyectar Pine de EMAs 20/50/100/200.
    - Si tienes TV_PUBLIC_LAYOUT_ID, igual funciona (inyecta encima si hace falta).
    - Timeouts amplios (120s), limpia popups y recorta el pane del chart.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright no está instalado. Ejecuta: pip install playwright && python -m playwright install chromium"
        ) from e

    url = _tv_url_for(symbol)
    outfile = f"{out_dir.rstrip('/')}/tv_{symbol.replace(':','_').replace('!','1')}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = browser.new_context(
            viewport={"width": 1360, "height": 800},
            locale="es-ES",
            timezone_id="UTC"
        )
        page = ctx.new_page()

        # Cargar TradingView
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        # Intento de aceptar cookies / cerrar popups (best-effort)
        for txt in ["Aceptar", "Acepto", "Accept", "Agree", "Estoy de acuerdo"]:
            try:
                page.get_by_role("button", name=txt, exact=False).click(timeout=1500)
                break
            except:
                pass
        for _ in range(2):
            for name in ["Cerrar", "Close"]:
                try:
                    page.get_by_role("button", name=name, exact=False).click(timeout=800)
                    break
                except:
                    pass
            try:
                page.keyboard.press("Escape")
            except:
                pass

        # Espera de red y respiro
        try:
            page.wait_for_load_state("networkidle", timeout=120_000)
        except:
            pass
        page.wait_for_timeout(3000)

        # --- Inyectar Pine con EMAs (best-effort) ---
        try:
            _inject_ema_pine(page)
        except Exception:
            pass

        # Intentar ubicar el canvas/pane del chart
        clip_done = False
        try:
            pane = page.locator('div[data-name="pane"]').first
            if pane and pane.is_visible():
                box = pane.bounding_box()
                if box:
                    page.screenshot(
                        path=outfile,
                        clip={
                            "x": max(0, box["x"] - 10),
                            "y": max(0, box["y"] - 10),
                            "width": box["width"] + 20,
                            "height": box["height"] + 20,
                        }
                    )
                    clip_done = True
        except:
            pass

        if not clip_done:
            # Fallback: canvas principal
            try:
                page.wait_for_selector('canvas[data-name="chart-canvas"]', timeout=5000)
                canvas = page.locator('canvas[data-name="chart-canvas"]').first
                box = canvas.bounding_box()
                if box:
                    page.screenshot(
                        path=outfile,
                        clip={
                            "x": max(0, box["x"] - 10),
                            "y": max(0, box["y"] - 10),
                            "width": box["width"] + 20,
                            "height": box["height"] + 20,
                        }
                    )
                    clip_done = True
            except:
                pass

        if not clip_done:
            # Fallback final: página completa
            page.screenshot(path=outfile, full_page=True)

        try:
            ctx.close()
            browser.close()
        except:
            pass

    return outfile



def add_alarm_tv(chat_id: int, symbol: str, price: float):
    sym  = (symbol or "").upper()
    ykey = _tv_key(sym)  # llave canónica por Yahoo

    # Si ya existe (mismo chat + misma llave canónica), ACTUALIZA
    for a in alarmas:
        if a.get("chat_id") == chat_id:
            ts = a.get("tv_symbol")
            if ts and _tv_key(ts) == ykey:
                a["tv_symbol"]       = sym            # mantenemos el último tv_symbol que llega
                a["precio_objetivo"] = float(price)
                a["cumple_prev"]     = None           # reset para detectar próximo cruce
                a.pop("ultimo_aviso", None)           # sin spam; será one-shot al cruce
                guardar_alarmas()
                print(f"🔄 Alarma TV ACTUALIZADA: {sym} → {float(price):.2f}")
                return

    # Si no existía, crear nueva
    alarma = {
        "chat_id": chat_id,
        "tv_symbol": sym,
        "precio_objetivo": float(price),
        "cumple_prev": None,   # para detectar cruce
    }
    alarmas.append(alarma)
    guardar_alarmas()
    print(f"📌 Alarma TV registrada: {sym} → {float(price):.2f}")

def mostrar_alarmas(chat_id: int):
    """
    Lista las alarmas activas del usuario:
    - TV: muestra objetivo, precio actual y si CUMPLE (price >= objetivo)
    - Otras: precio/volumen/EMA/RSI tal como ya las manejas
    """
    lineas = []
    try:
        # --- TV ---
        for a in alarmas:
            if a.get("chat_id") != chat_id:
                continue
            sym = a.get("tv_symbol")
            if sym:
                target = float(a.get("precio_objetivo", 0.0))
                price = obtener_precio(sym)
                if price is None:
                    lineas.append(f"• TV: {sym} objetivo {target:.2f} | actual N/D → ⚠️ sin precio")
                else:
                    marca = "✅ CUMPLE" if price >= target else "❌ NO CUMPLE"
                    lineas.append(f"• TV: {sym} objetivo {target:.2f} | actual {price:.2f} → {marca}")

        # --- OTRAS (precio/volumen/EMA/RSI) ---
        for a in alarmas:
            if a.get("chat_id") != chat_id:
                continue
            if a.get("tv_symbol"):
                continue
            if "precio_objetivo" in a and a.get("moneda"):
                lineas.append(f"• PRECIO: {a['moneda'].upper()} a ${a['precio_objetivo']:.2f}")
            elif "tipo_volumen" in a:
                lineas.append(f"• VOLUMEN: {a['moneda'].upper()} {a['tipo_volumen'].upper()} > {a['umbral']}")
            elif "ema_period" in a:
                tol_cfg = a.get("tolerancia", "auto")
                tol_txt = "auto" if (isinstance(tol_cfg, str) and tol_cfg.lower() == "auto") else f"±{float(tol_cfg)*100:.4f}%"
                lineas.append(f"• EMA: {a['moneda'].upper()} EMA{a['ema_period']} @ {a.get('intervalo','1h')} ({tol_txt})")
            elif a.get("alarm_type") == "rsi_div":
                lineas.append(f"• RSI-DIV: {a['symbol']} {a['direction'].upper()} {a['intervalo']} {a['rango']}")

        if not lineas:
            send_message(chat_id, "⛔ No tienes alarmas activas.")
            return

        msg = "🔔 *Tus alarmas*\n" + "\n".join(lineas)
        if len(msg) > 3800:
            msg = msg[:3790] + "…"
        send_message(chat_id, msg)
    except Exception as e:
        # Si algo raro pasa, al menos responde algo útil
        send_message(chat_id, f"⚠️ No pude listar las alarmas: {e}")



def dedupe_alarmas_tv():
    """
    Deja solo UNA alarma TV por (chat_id, ykeyYahoo). Conserva la ÚLTIMA entrada.
    Normaliza campos para 'cross_up' one-shot (cumple_prev=None; sin ultimo_aviso).
    Mantiene alarmas no-TV como están.
    """
    unique = {}
    non_tv = []
    for a in alarmas:
        ts = a.get("tv_symbol")
        if ts:
            key = (a.get("chat_id"), _tv_key(ts))
            unique[key] = a  # la última sobreescribe las previas
        else:
            non_tv.append(a)

    tv_clean = []
    for (_, _), a in unique.items():
        a.setdefault("cumple_prev", None)
        a.pop("ultimo_aviso", None)
        tv_clean.append(a)

    alarmas[:] = non_tv + tv_clean
    guardar_alarmas()
    print(f"🧹 Dedupe TV: {len(tv_clean)} TV + {len(non_tv)} otras (total {len(alarmas)})")


def remove_alarm_tv(chat_id: int, symbol: str) -> bool:
    sym = symbol.upper()
    prev = len(alarmas)
    alarmas[:] = [a for a in alarmas if not (a["chat_id"] == chat_id and a.get("tv_symbol") == sym)]
    if len(alarmas) < prev:
        guardar_alarmas()
        return True
    return False

def verificar_alarmas_tv_tick():
    import time
    now = time.time()

    # Recorremos una copia porque vamos a eliminar elementos
    for alarma in list(alarmas):
        sym = alarma.get("tv_symbol")
        if not sym:
            continue

        chat_id = alarma["chat_id"]
        target = float(alarma.get("precio_objetivo", 0.0))

        price = obtener_precio(sym)
        if price is None:
            print(f"⚠️ Precio no disponible para {sym}.")
            continue

        # Log en cada revisión
        cumple = price >= target
        print(
            f"🔎 Alarma {sym} → objetivo {target:.2f} | "
            f"precio actual {price:.2f} → {'✅ CUMPLE' if cumple else '❌ NO CUMPLE'}"
        )

        # Guarda el estado anterior
        prev = alarma.get("cumple_prev", None)
        alarma["cumple_prev"] = cumple

        # Solo enviar cuando pase de NO cumplir -> a CUMPLIR
        if prev is not None and not prev and cumple:
            send_message(chat_id, f"🚨 {sym}: {price:.2f} ≥ objetivo {target:.2f}")
            alarmas.remove(alarma)
            guardar_alarmas()




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
    # --- Alarmas de precio (CoinGecko) SOLO cripto (no TV)
    for alarma in alarmas[:]:
        try:
            if ("precio_objetivo" in alarma) and ("moneda" in alarma) and (not alarma.get("tv_symbol")):
                moneda = alarma["moneda"]
                chat_id = alarma["chat_id"]

                r = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": moneda, "vs_currencies": "usd"},
                    timeout=20
                )
                if r.status_code == 200:
                    data = r.json()
                    precio_actual = data.get(moneda, {}).get("usd", 0)
                    if precio_actual >= float(alarma["precio_objetivo"]):
                        send_message(chat_id, f"🚨 ¡{moneda.upper()} ha alcanzado ${precio_actual:.2f}!")
                        alarmas.remove(alarma)
                        guardar_alarmas()
        except Exception as e:
            print(f"⚠️ Error verificando alarma de precio: {e}")

    # --- Alarmas de volumen (verde/rojo) usando mini-API interna
    for alarma in alarmas[:]:
        try:
            if ("tipo_volumen" in alarma) and ("moneda" in alarma):
                moneda = alarma["moneda"]
                chat_id = alarma["chat_id"]
                tipo = alarma["tipo_volumen"]  # 'verde' | 'rojo'
                umbral = float(alarma.get("umbral", 30000 if tipo == "verde" else 60000))

                r = requests.get(CRYPTO_API, params={"name": moneda}, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    volumen = data.get("volumen_verde" if tipo == "verde" else "volumen_rojo", 0)
                    if float(volumen) > umbral:
                        color = "🟢" if tipo == "verde" else "🔴"
                        send_message(chat_id, f"{color} ¡Alerta! Volumen {tipo.upper()} de {moneda.upper()} supera {umbral}: {volumen}")
                        alarmas.remove(alarma)
                        guardar_alarmas()
        except Exception as e:
            print(f"⚠️ Error verificando volumen: {e}")

    # --- Alarmas de EMA (precio toca EMA N con tolerancia 'auto' o manual)
    for alarma in alarmas[:]:
        try:
            if all(k in alarma for k in ("moneda", "intervalo", "ema_period")):
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
            print(f"⚠️ Error verificando alarma EMA: {e}")


def verificar_alarmas_rsi_div_tick():
    """
    Revisa alarmas de divergencias RSI (alcista/bajista) y dispara cuando se forma
    una nueva en las velas recientes (usamos timestamp del último swing).
    """
    for alarma in alarmas[:]:
        if alarma.get("alarm_type") != "rsi_div":
            continue
        symbol = alarma.get("symbol")
        intervalo = alarma.get("intervalo", "60m")
        rango = alarma.get("rango", "1mo")
        chat_id = alarma["chat_id"]
        try:
            path, last_bull, last_bear = generate_rsi_divergence_chart(symbol, out_dir="/tmp", interval=intervalo, range_=rango)
            ts_ref = alarma.get("last_ts")
            triggered = False
            if alarma["direction"] == "alcista" and last_bull and last_bull != ts_ref:
                triggered = True; alarma["last_ts"] = last_bull
            if alarma["direction"] == "bajista" and last_bear and last_bear != ts_ref:
                triggered = True; alarma["last_ts"] = last_bear
            if triggered:
                with open(path, "rb") as photo:
                    files = {"photo": photo}
                    caption = f"🚨 Divergencia RSI(14) {alarma['direction'].upper()} detectada • {symbol} • {intervalo}"
                    requests.post(f"{API_URL}/sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": photo}, timeout=60)
                alarmas.remove(alarma); guardar_alarmas()
        except Exception as e:
            print(f"⚠️ Error verificador RSI divergencia: {e}")
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
    cmd = text.lower().split()[0] if text else ""
    if cmd == "/alarmas":
        mostrar_alarmas(chat_id)
        return

    # --- /rsi_div <símbolo> [intervalo] [rango] ---
    if text.lower().startswith("/rsi_div"):
        partes = text.split()
        if len(partes) < 2:
            send_message(chat_id, "❌ Uso: /rsi_div <símbolo> [intervalo] [rango]\nEj: /rsi_div MES1! 60m 1mo")
            return
        symbol = partes[1].upper()
        intervalo = partes[2] if len(partes) >= 3 else "60m"
        rango = partes[3] if len(partes) >= 4 else "1mo"
        try:
            path, last_bull, last_bear = generate_rsi_divergence_chart(symbol, out_dir="/tmp", interval=intervalo, range_=rango)
            caption = f"📈 RSI(14) Divergencias • {symbol} • {intervalo} • {rango}"
            with open(path, "rb") as photo:
                files = {"photo": photo}
                data = {"chat_id": chat_id, "caption": caption}
                requests.post(f"{API_URL}/sendPhoto", data=data, files=files, timeout=60)
        except Exception as e:
            send_message(chat_id, f"⚠️ No pude generar RSI(14) divergencias: {e}")
        return

    # --- /alarma rsi_div <símbolo> <alcista|bajista> [intervalo] [rango] ---
    if text.lower().startswith("/alarma rsi_div"):
        partes = text.split()
        if len(partes) < 4:
            send_message(chat_id, "❌ Uso: /alarma rsi_div <símbolo> <alcista|bajista> [intervalo] [rango]\nEj: /alarma rsi_div MES1! alcista 60m 1mo")
            return
        symbol = partes[2].upper()
        direction = partes[3].lower()
        if direction not in ("alcista", "bajista"):
            send_message(chat_id, "❌ Tipo debe ser 'alcista' o 'bajista'.")
            return
        intervalo = partes[4] if len(partes) >= 5 else "60m"
        rango = partes[5] if len(partes) >= 6 else "1mo"
        alarmas.append({
            "chat_id": chat_id,
            "alarm_type": "rsi_div",
            "symbol": symbol,
            "direction": direction,
            "intervalo": intervalo,
            "rango": rango,
            "last_ts": None
        })
        guardar_alarmas()
        send_message(chat_id, f"⏰ Alarma RSI divergencia {direction.upper()} creada para {symbol} • {intervalo} • {rango}")
        return

    # --- Nuevos comandos: /tvema y /alarma tv ---
    if text.lower().startswith("/tvema"):
        partes = text.split()
        if len(partes) < 2:
            send_message(chat_id, "❌ Uso: /tvema <símbolo>\nEjemplo: /tvema MES1!")
            return
        symbol = partes[1].upper()
        tv_url = _tv_url_for(symbol)
        try:
            screenshot_path = get_tradingview_screenshot(symbol, out_dir="/tmp")
            with open(screenshot_path, "rb") as photo:
                files = {"photo": photo}
                data = {"chat_id": chat_id, "caption": f"📊 TradingView {symbol}\n{tv_url}"}
                requests.post(f"{API_URL}/sendPhoto", data=data, files=files, timeout=60)
        except Exception as e:
            send_message(chat_id, f"⚠️ No pude generar la captura ({e}). Aquí el gráfico:\n{tv_url}")
        return

    if text.lower().startswith("/alarma tv"):
        partes = text.split()
        if len(partes) != 4:
            send_message(chat_id, "❌ Uso: /alarma tv <símbolo> <precio>\nEjemplo: /alarma tv MES1! 5000")
            return
        symbol = partes[2].upper()
        try:
            price = float(partes[3])
        except ValueError:
            send_message(chat_id, "❌ El precio debe ser numérico. Ej: 5000")
            return
        add_alarm_tv(chat_id, symbol, price)
        send_message(chat_id, f"⏰ Alarma TV creada: {symbol} → {price}")
        return

    if text.lower().startswith("/eliminaralarma tv"):
        partes = text.split()
        if len(partes) != 3:
            send_message(chat_id, "❌ Uso: /eliminaralarma tv <símbolo>\nEjemplo: /eliminaralarma tv MES1!")
            return
        symbol = partes[2].upper()
        if remove_alarm_tv(chat_id, symbol):
            send_message(chat_id, f"🗑️ Alarma TV de {symbol} eliminada.")
        else:
            send_message(chat_id, "⚠️ No había esa alarma TV activa.")
        return

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
            "🌍 *Recursos Externos*\n"
            "• /tvema <símbolo> [intervalo]\n"
            "  └ Muestra el gráfico de TradingView (enlace con captura incluida) para el recurso indicado. Puedes pasar intervalo (ej: 4h, 1h, 1D).\n"
            "• /alarma tv <símbolo> <precio>\n"
            "  └ Te aviso cuando el precio *toque o supere* ese valor en TradingView.\n"
            "• /alarma tv_ema <símbolo> <intervalo> <periodo> [tolerancia%|auto]\n"
            "  └ Alarma por *toque de EMA* usando datos Yahoo (ej: /alarma tv_ema MES1! 4h 50 auto).\n"
            "• /tvema_emas <símbolo> [intervalo] [rango]\n"
            "  └ Gráfico *propio* con EMAs 20/50/100/200 (Yahoo). Asegura EMAs aunque TV no deje Pine.\n"
            "\n"
            "📊 *Indicadores RSI*\n"
            "• /rsi_div <símbolo> [intervalo] [rango]\n"
            "  └ Muestra un gráfico con RSI(14) y marca divergencias *alcistas/bajistas*.\n"
            "• /alarma rsi_div <símbolo> <alcista|bajista> [intervalo] [rango]\n"
            "  └ Crea una alarma que te avisa cuando aparezca una *nueva* divergencia RSI.\n"
            "\n"
            "🗂️ *Gestión*\n"
            "• /alarmas\n"
            "  └ Lista todas tus alarmas activas (precio, volumen, EMA y RSI-div).\n"
            "• /crypto precio <moneda>\n"
            "  └ Precio rápido + volumen actual (consulta interna).\n"
            "\n"
            "🧭 *Notas*\n"
            "• *Intervalos válidos:* 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d\n"
            "• Puedes usar nombres o símbolos comunes (ej: *bitcoin* o *btc*, *dogecoin* o *doge*, *MES1!* para Micro E-Mini S&P 500).\n"
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
            "• /tvema MES1! 4h\n"
            "• /alarma tv MES1! 5000\n"
            "• /alarma tv_ema MES1! 4h 50 auto\n"
            "• /tvema_emas MES1! 60m 1mo\n"
            "• /rsi_div MES1! 60m 1mo\n"
            "• /alarma rsi_div MES1! alcista 60m 1mo\n"
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

    elif text.lower().startswith("/alarmas"):
        mostrar_alarmas(chat_id)



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
    import random
    import time
    print("📱 Escuchando mensajes del bot...")

    # 🔧 Limpieza de duplicados al iniciar (evita múltiples MES1! o MES! en alarmas.json)
    try:
        dedupe_alarmas_tv()
    except Exception as e:
        print(f"ℹ️ Dedupe omitido: {e}")

    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(update["message"])

            # === Revisión de alarmas ===
            verificar_alarmas_tick()         # alarmas de precio / RSI / etc.
            verificar_alarmas_tv_tick()      # alarmas tipo TradingView
            verificar_alarmas_rsi_div_tick() # alarmas RSI-divergencias

            # === Log en consola ===
            ahora = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"⏱ [{ahora}] Revisión completada — Alarmas revisadas correctamente.")

        except Exception as e:
            print(f"⚠️ Error en el ciclo principal: {e}")

        # Espera aleatoria entre 30 y 60 segundos
        time.sleep(random.uniform(30, 60))

# --- Servidor Flask interno (5001) en segundo plano ---
threading.Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    main()
