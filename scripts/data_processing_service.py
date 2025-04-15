import pandas as pd
import numpy as np
from scipy import stats

class DataProcessingService:
    def __init__(self):
        pass

    def clean_data(self, df):
        print("Eliminando duplicados y valores nulos...")
        df = df.drop_duplicates()
        df = df.dropna()
        return df

    def convert_columns(self, df, datetime_cols=[], numeric_cols=[], category_cols=[]):
        print("Convirtiendo tipos de columnas...")
        for col in datetime_cols:
            df[col] = pd.to_datetime(df[col], errors='coerce')
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        for col in category_cols:
            df[col] = df[col].astype('category')
        return df

    def calculate_derived_metrics(self, df, price_col='price', timestamp_col='timestamp'):
        print("Calculando métricas derivadas...")
        df = df.sort_values(by=timestamp_col)
        df['daily_return'] = df[price_col].pct_change()
        df['moving_avg_7'] = df[price_col].rolling(window=7).mean()
        df['moving_avg_30'] = df[price_col].rolling(window=30).mean()
        return df

    def detect_and_handle_outliers(self, df, cols):
        print("Detectando y tratando outliers...")
        for col in cols:
            z_scores = np.abs(stats.zscore(df[col].dropna()))
            outlier_indices = z_scores > 3
            df.loc[df[col].dropna().index[outlier_indices], col] = np.nan  # reemplaza outliers con NaN
        df = df.fillna(method='ffill').fillna(method='bfill')  # rellena hacia adelante y hacia atrás
        return df
