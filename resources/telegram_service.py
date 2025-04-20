from flask_restful import Resource
from flask import request
from flasgger import swag_from
import requests

TELEGRAM_BOT_TOKEN = "7674766197:AAFB6QlNXkspIXejhmz8rCJGM-yDAm9I5u8"

class TelegramMessageResource(Resource):
    @swag_from({
        'tags': ['Telegram'],
        'consumes': ['application/json'],
        'parameters': [
            {
                'name': 'body',
                'in': 'body',
                'required': True,
                'schema': {
                    'type': 'object',
                    'properties': {
                        'chat_id': {'type': 'string'},
                        'mensaje': {'type': 'string'}
                    },
                    'required': ['chat_id', 'mensaje']
                }
            }
        ],
        'responses': {
            200: {
                'description': 'Mensaje enviado correctamente',
                'examples': {
                    'application/json': {
                        'status': 'success',
                        'message': 'Mensaje enviado a Telegram'
                    }
                }
            },
            500: {
                'description': 'Error al enviar el mensaje'
            }
        }
    })
    def post(self):
        data = request.get_json()
        chat_id = data.get('chat_id')
        mensaje = data.get('mensaje')

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje}

        response = requests.post(url, json=payload)

        if response.status_code == 200:
            return {'status': 'success', 'message': 'Mensaje enviado'}
        else:
            return {'status': 'error', 'message': 'No se pudo enviar'}, 500
