import time
from apscheduler.schedulers.background import BackgroundScheduler

from scripts.data_ingestion_service import DataIngestionService
from scripts.data_processing_service import DataProcessingService

COIN = "bitcoin"
INTERVALOS = ["4H", "1D"]
START_DATE = "2024-10-01"
INTERVAL_MINUTES = 5   # puedes cambiar a 10 si quieres

def job():
    print("⏱️ Ejecutando job de ingesta...")
    ingest = DataIngestionService(coin_id=COIN)
    proc = DataProcessingService()

    for intervalo in INTERVALOS:
        print(f"📊 Procesando intervalo {intervalo}")

        df = ingest.fetch_data(
            intervalo=intervalo,
            start_date=START_DATE
        )

        df = proc.clean_data(df)
        df = proc.convert_columns(
            df,
            datetime_cols=["timestamp"],
            numeric_cols=["precio"],
            category_cols=["coin"]
        )

        df = proc.calculate_derived_metrics(
            df,
            price_col="precio",
            timestamp_col="timestamp"
        )

        df = proc.detect_and_handle_outliers(df, cols=["precio"])
        ingest.save_to_postgres(df)

    print("✅ Guardado en PostgreSQL (4H y 1D)")

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        job,
        "interval",
        minutes=INTERVAL_MINUTES,
        max_instances=1,
        coalesce=True
    )
    scheduler.start()

    print(f"🚀 Scheduler activo: cada {INTERVAL_MINUTES} minutos")
    job()  # corre una vez al iniciar

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()
        print("🛑 Scheduler detenido")
