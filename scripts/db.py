import os
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def pg_engine():
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    db   = os.getenv("PGDATABASE", "crypto_db")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")
    url = f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"
    return create_engine(url)

def get_latest_price(coin: str = "bitcoin"):
    engine = pg_engine()
    q = text("""
        SELECT coin, timestamp, price
        FROM crypto_prices
        WHERE coin = :coin
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    with engine.begin() as conn:
        row = conn.execute(q, {"coin": coin}).fetchone()
    return row  # None si no hay
