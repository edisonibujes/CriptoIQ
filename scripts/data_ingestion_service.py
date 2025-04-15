import os
import pandas as pd
import matplotlib.pyplot as plt
from pycoingecko import CoinGeckoAPI
from datetime import datetime

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
