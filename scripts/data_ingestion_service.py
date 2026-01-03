
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

    def fetch_data(self, vs_currency='usd'):
        print(f"Obteniendo datos de {self.coin_id} desde CoinGecko (últimos {self.days} días)...")
        data = self.cg.get_coin_market_chart_by_id(id=self.coin_id, vs_currency=vs_currency, days=self.days)
        df = pd.DataFrame(data['prices'], columns=['timestamp', 'price'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['coin'] = self.coin_id
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
        """
        Guarda en PostgreSQL evitando duplicados por (coin, timestamp).
        Requiere UNIQUE(coin, timestamp) en la tabla.
        """
        cols = [c for c in ["coin", "timestamp", "price", "daily_return", "moving_avg_7", "moving_avg_30"] if c in df.columns]
        df2 = df[cols].copy()

        engine = self._pg_engine()
        with engine.begin() as conn:
            df2.to_sql("stg_crypto_prices", conn, if_exists="replace", index=False)

            conn.execute(text(f"""
                INSERT INTO {table_name} (coin, timestamp, price, daily_return, moving_avg_7, moving_avg_30)
                SELECT coin, timestamp, price, daily_return, moving_avg_7, moving_avg_30
                FROM stg_crypto_prices
                ON CONFLICT (coin, timestamp) DO UPDATE SET
                  price = EXCLUDED.price,
                  daily_return = EXCLUDED.daily_return,
                  moving_avg_7 = EXCLUDED.moving_avg_7,
                  moving_avg_30 = EXCLUDED.moving_avg_30;
            """))

