
import pandas as pd
import matplotlib.pyplot as plt
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv()

from pycoingecko import CoinGeckoAPI
from datetime import datetime


load_dotenv()

class DataIngestionService:
    def __init__(self, coin_id='bitcoin', days=30, output_dir='data'):
        self.cg = CoinGeckoAPI()
        self.coin_id = coin_id
        self.days = days
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def fetch_data(
        self,
        vs_currency: str = "usd",   # (no se usa con Binance, lo dejamos para compatibilidad)
        intervalo: str = "4H",
        start_date: str = "2024-10-01",
        symbol: str = "BTCUSDT"
    ):
        import pandas as pd
        import requests
        import time

        # 1) Mapear tus intervalos a los de Binance
        interval_map = {
            "4H": "4h",
            "4h": "4h",
            "1D": "1d",
            "1d": "1d",
        }
        if intervalo not in interval_map:
            raise ValueError(f"Intervalo no soportado: {intervalo}. Usa '4H' o '1D'.")

        binance_interval = interval_map[intervalo]

        print(f"Obteniendo velas Binance {symbol} desde {start_date} con intervalo {intervalo}...")

        start_ts = int(pd.to_datetime(start_date, utc=True).timestamp() * 1000)
        end_ts = int(pd.Timestamp.utcnow().timestamp() * 1000)

        url = "https://api.binance.com/api/v3/klines"

        all_rows = []
        cur = start_ts
        limit = 1000

        # 2) Paginación: Binance devuelve máx 1000 velas por request
        while cur < end_ts:
            params = {
                "symbol": symbol,
                "interval": binance_interval,
                "startTime": cur,
                "endTime": end_ts,
                "limit": limit
            }

            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            if not data:
                break

            all_rows.extend(data)

            # avanzar al siguiente bloque: usar openTime del último + 1ms
            last_open_time = data[-1][0]
            cur = last_open_time + 1

            # pequeño sleep para no pegarle duro a Binance
            time.sleep(0.2)

            # si Binance te devuelve siempre lo mismo por alguna razón, evita loop infinito
            if len(data) < limit:
                break

        # 3) Convertir a DataFrame
        # Kline format:
        # 0 open_time, 1 open, 2 high, 3 low, 4 close, 5 volume, 6 close_time, ...
        df = pd.DataFrame(all_rows, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "num_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])

        # timestamp: usamos open_time como marca de vela
        df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        df["precio"] = pd.to_numeric(df["close"], errors="coerce")

        # coin: puedes guardar 'bitcoin' o el símbolo; dejo 'bitcoin' para que coincida con tu Excel
        df["coin"] = getattr(self, "coin_id", "bitcoin") or "bitcoin"
        df["intervalo"] = "4H" if binance_interval == "4h" else "1D"

        df = df[["coin", "timestamp", "precio", "intervalo"]].dropna()

        return df





    def save_to_csv(self, df, filename=None):
        if not filename:
            filename = f'{self.coin_id}_data.csv'
        path = os.path.join(self.output_dir, filename)
        df.to_csv(path, index=False)
        print(f"Datos guardados en: {path}")

    def load_from_local(self, filename=None):
        if not filename:
            filename = f'{self.coin_id}_data.csv'
        path = os.path.join(self.output_dir, filename)
        print(f"Cargando datos desde: {path}")
        return pd.read_csv(path)

    def filter_by_coin(self, df, coin_name=None):
        if coin_name is None:
            coin_name = self.coin_id
        print(f"Filtrando datos para: {coin_name}")
        return df[df['coin'].str.lower() == coin_name.lower()]

    def plot_price(self, df):
        print(f"Mostrando gráfico de precios de {self.coin_id.capitalize()}...")
        plt.figure(figsize=(10, 5))
        plt.plot(df['timestamp'], df['price'], label=f'{self.coin_id.upper()} Price')
        plt.xlabel('Fecha')
        plt.ylabel('Precio en USD')
        plt.title(f'Precio Histórico de {self.coin_id.capitalize()}')
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        plt.show()

    def _pg_engine(self):
        host = os.getenv("PGHOST", "localhost")
        port = os.getenv("PGPORT", "5432")
        db   = os.getenv("PGDATABASE", "crypto_db")
        user = os.getenv("PGUSER", "postgres")
        pwd  = os.getenv("PGPASSWORD", "")
        url = f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"
        return create_engine(url)

    def save_to_postgres(self, df, table_name="crypto_prices"):
        cols = [c for c in [
            "coin", "timestamp", "precio", "intervalo",
            "moving_avg_20", "moving_avg_50", "moving_avg_100", "moving_avg_200"
        ] if c in df.columns]

        df2 = df[cols].copy()

        engine = self._pg_engine()
        with engine.begin() as conn:
            df2.to_sql("stg_crypto_prices", conn, if_exists="replace", index=False)

            conn.execute(text(f"""
                INSERT INTO {table_name} (coin, timestamp, precio, intervalo, moving_avg_20, moving_avg_50, moving_avg_100, moving_avg_200)
                SELECT coin, timestamp, precio, intervalo, moving_avg_20, moving_avg_50, moving_avg_100, moving_avg_200
                FROM stg_crypto_prices
                ON CONFLICT (coin, timestamp, intervalo) DO UPDATE SET
                precio = EXCLUDED.precio,
                moving_avg_20 = EXCLUDED.moving_avg_20,
                moving_avg_50 = EXCLUDED.moving_avg_50,
                moving_avg_100 = EXCLUDED.moving_avg_100,
                moving_avg_200 = EXCLUDED.moving_avg_200;
            """))

