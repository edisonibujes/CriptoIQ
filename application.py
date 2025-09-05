from flask import Flask, jsonify, redirect
from flask_restful import Api, MethodNotAllowed, NotFound
from flask_cors import CORS
from util.common import domain, port, prefix, build_swagger_config_json
print(f"📍 API registrada con prefijo: {prefix}")

# Recursos RESTful
from resources.swaggerConfig import SwaggerConfig
from resources.bookResource import BooksGETResource, BookGETResource, BookPOSTResource, BookPUTResource, BookDELETEResource
from resources.crypto_resource import CryptoHistoricalResource, CryptoCurrentPriceResource
from resources.telegram_service import TelegramMessageResource
from resources.ema_graph_resource import EMAGraphResource  # ✅ solo una vez

from flasgger import Swagger
from scripts.data_ingestion_service import DataIngestionService
from scripts.data_processing_service import DataProcessingService

# ============================================
# Main
# ============================================
application = Flask(__name__)
swagger = Swagger(application)
app = application
app.config['PROPAGATE_EXCEPTIONS'] = True
CORS(app)

api = Api(app, prefix=prefix, catch_all_404s=True)

# Registrar recursos
api.add_resource(EMAGraphResource, '/ema-graph')  # ✅ aquí va perfectamente


# ============================================
# Error Handler
# ============================================


@app.errorhandler(NotFound)
def handle_method_not_found(e):
    response = jsonify({"message": str(e)})
    response.status_code = 404
    return response


@app.errorhandler(MethodNotAllowed)
def handle_method_not_allowed_error(e):
    response = jsonify({"message": str(e)})
    response.status_code = 405
    return response


@app.route('/')
def redirect_to_prefix():
    return redirect("/apidocs")  # Redirige directamente a Swagger UI


# ============================================
# Add Resource
# ============================================
api.add_resource(CryptoHistoricalResource, '/crypto-data')           # historial
api.add_resource(CryptoCurrentPriceResource, '/crypto-data-actual')  # precio actual
api.add_resource(TelegramMessageResource, '/enviar-mensaje-telegram')




# GET swagger config
api.add_resource(SwaggerConfig, '/swagger-config')
# GET books
api.add_resource(BooksGETResource, '/books')
api.add_resource(BookGETResource, '/books/<int:id>')
# POST book
api.add_resource(BookPOSTResource, '/books')
# PUT book
api.add_resource(BookPUTResource, '/books/<int:id>')
# DELETE book
api.add_resource(BookDELETEResource, '/books/<int:id>')


# Mostrar todos los endpoints registrados
for rule in app.url_map.iter_rules():
    print(f"📍 Ruta registrada: {rule}")

if __name__ == '__main__':
    app.run(debug=True)
