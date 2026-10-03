from flask import Flask

from app.config import load_config
from app.storage.database import Database


def create_app(config_override=None):
    app = Flask(__name__)
    app.config.update(load_config())
    if config_override:
        app.config.update(config_override)

    database = Database(app.config["DATABASE_PATH"])
    database.initialize()
    app.extensions["database"] = database

    from app.web.routes import web

    app.register_blueprint(web)
    return app
