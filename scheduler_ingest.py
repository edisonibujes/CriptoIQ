import time
from apscheduler.schedulers.background import BackgroundScheduler

from scripts.data_ingestion_service import DataIngestionService
from scripts.data_processing_service import DataProcessingService

COIN = "bitcoin"
DAYS = 30
INTERVAL_MINUTES = 15  # <-- cámbialo a lo que quieras (5, 10, 60, etc.)

def job():
    print("⏱️ Ejecutando job de ingesta...")
    ingest = DataIngestionService(coin_id=COIN, days=DAYS)
    proc = DataProcessingService()

    df = ingest.fetch_data()
    df = proc.clean_data(df)
    df = proc.convert_columns(df, datetime_cols=["timestamp"], numeric_cols=["price"], category_cols=["coin"])
    df = proc.calculate_derived_metrics(df, price_col="price", timestamp_col="timestamp")
    df = proc.detect_and_handle_outliers(df, cols=["price"])

    ingest.save_to_postgres(df)  # usa tu ON CONFLICT (no duplica)
    print("✅ Guardado en PostgreSQL")

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(job, "interval", minutes=INTERVAL_MINUTES, max_instances=1, coalesce=True)
    scheduler.start()

    print(f"🚀 Scheduler activo: cada {INTERVAL_MINUTES} minutos → PostgreSQL")
    job()  # corre una vez al iniciar

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()
        print("🛑 Scheduler detenido")
