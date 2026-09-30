import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # host/port come from app.config (Config.from_env(), app/config.py) so the
    # loopback-only bind check runs -- unlike `flask run --host ...`, which
    # binds Werkzeug directly and never consults app.config["HOST"] at all.
    app.run(host=app.config["HOST"], port=app.config["PORT"], debug=os.environ.get("FLASK_DEBUG") == "1")
