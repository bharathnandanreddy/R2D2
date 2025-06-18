from flask import Flask
from google.cloud import storage
import os

def create_app():
    app = Flask(__name__)
    app.config.from_pyfile('../config.py')

    # Set up GCS client (if you want it app-wide)
    # app.gcs_client = storage.Client(project=app.config['PROJECT_ID'])

    from .routes import main
    app.register_blueprint(main)

    return app
