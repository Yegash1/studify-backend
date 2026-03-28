# app.py
from flask import Flask, send_file
from flask_cors import CORS
from config import Config
from extensions import db, jwt, bcrypt, socketio, mail

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.url_map.strict_slashes = False

    CORS(app)

    db.init_app(app)
    jwt.init_app(app)
    bcrypt.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")
    mail.init_app(app)

    from routes.auth         import auth_bp
    from routes.spaces       import spaces_bp
    from routes.reservations import res_bp
    from routes.ratings      import ratings_bp
    app.register_blueprint(auth_bp,     url_prefix="/api/auth")
    app.register_blueprint(spaces_bp,   url_prefix="/api/spaces")
    app.register_blueprint(res_bp,      url_prefix="/api/reservations")
    app.register_blueprint(ratings_bp,  url_prefix="/api/ratings")

    return app

app = create_app()

@app.route('/')
def index():
    try:
        db.session.execute(db.text('SELECT 1'))
        return {"message": "Studify API is running! 🎉", "database": "Connected ✅"}
    except Exception as e:
        return {"message": "Studify API is running!", "database": f"NOT connected ❌ — {str(e)}"}

@app.route('/app')
def frontend():
    return send_file('studify.html')

if __name__ == "__main__":
    socketio.run(app, debug=True, port=4000)