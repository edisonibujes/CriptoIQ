# crypto_resource.py
from flask_restful import Resource
from flask import request
from scripts.data_ingestion_service import DataIngestionService
from scripts.data_processing_service import DataProcessingService

from flasgger import swag_from
import requests

class CryptoHistoricalResource(Resource):
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
            df['timestamp'] = df['timestamp'].astype(str)
            return {
                "status": "success",
                "coin": coin_id,
                "days": days,
                "records": len(df),
                "data": df.tail(10).to_dict(orient='records')
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}, 500


class CryptoCurrentPriceResource(Resource):
    @swag_from({
        'tags': ['Criptomonedas'],
        'parameters': [
            {
                'name': 'name',
                'in': 'query',
                'type': 'string',
                'required': True,
                'description': 'Nombre de la criptomoneda (ej: bitcoin, ethereum)'
            }
        ],
        'responses': {
            200: {
                'description': 'Precio actual de la criptomoneda',
                'examples': {
                    'application/json': {
                        'moneda': 'bitcoin',
                        'precio_usd': 29485.23
                    }
                }
            },
            404: {
                'description': 'Criptomoneda no encontrada'
            }
        }
    })
    def get(self):
        coin_name = request.args.get('name')
        if not coin_name:
            return {"error": "Falta el parámetro 'name'"}, 400

        url = "https://api.coingecko.com/api/v3/simple/price"
        response = requests.get(url, params={'ids': coin_name, 'vs_currencies': 'usd'})

        if response.status_code != 200 or coin_name not in response.json():
            return {"error": f"No se encontró '{coin_name}'"}, 404

        return {
            'moneda': coin_name,
            'precio_usd': response.json()[coin_name]['usd']
        }


