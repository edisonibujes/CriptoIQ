from flask_restful import Resource
from flask import request
from scripts.data_ingestion_service import DataIngestionService
from flasgger import swag_from

class CryptoPriceResource(Resource):
    @swag_from({
        'responses': {
            200: {
                'description': 'Datos de precios históricos',
                'examples': {
                    'application/json': {
                        "status": "success",
                        "coin": "bitcoin",
                        "records": 5,
                        "data": [
                            {
                                "timestamp": "2025-04-10T00:00:00",
                                "price": 70000.0,
                                "coin": "bitcoin"
                            }
                        ]
                    }
                }
            }
        },
        'parameters': [
            {
                'name': 'coin',
                'in': 'query',
                'type': 'string',
                'required': False,
                'description': 'ID de la criptomoneda (ej: bitcoin)'
            },
            {
                'name': 'days',
                'in': 'query',
                'type': 'integer',
                'required': False,
                'description': 'Número de días hacia atrás (ej: 30)'
            }
        ],
        'tags': ['Cripto']
    })
    def get(self):
        coin_id = request.args.get('coin', 'bitcoin')
        days = int(request.args.get('days', 30))
        try:
            service = DataIngestionService(coin_id=coin_id, days=days)
            df = service.fetch_data()
            df['timestamp'] = df['timestamp'].astype(str)  # 👈 CORRECCIÓN
            return {
                "status": "success",
                "coin": coin_id,
                "days": days,
                "records": len(df),
                "data": df.tail(10).to_dict(orient='records')
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}, 500
