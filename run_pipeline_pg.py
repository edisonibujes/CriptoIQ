from scripts.data_ingestion_service import DataIngestionService
from scripts.data_processing_service import DataProcessingService

if __name__ == "__main__":
    ingest = DataIngestionService(coin_id="bitcoin", days=30)
    proc = DataProcessingService()

    # 1) Ingesta
    df = ingest.fetch_data()

    # 2) Procesamiento
    df = proc.clean_data(df)
    df = proc.convert_columns(
        df,
        datetime_cols=["timestamp"],
        numeric_cols=["price"],
        category_cols=["coin"]
    )
    df = proc.calculate_derived_metrics(
        df,
        price_col="price",
        timestamp_col="timestamp"
    )
    df = proc.detect_and_handle_outliers(df, cols=["price"])

    # 3) Guardar en PostgreSQL
    ingest.save_to_postgres(df)

    print("✅ Pipeline ejecutado correctamente → PostgreSQL")
