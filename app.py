"""School Examination Management System.

Run locally with:   python app.py
Then open:           http://127.0.0.1:5000

All data is stored in the "data" folder next to this file - nothing is
sent anywhere else. First login is username "admin", password "admin123"
(you will be asked to set a new password immediately).
"""
import os
import webbrowser
from threading import Timer

from flask import Flask, g, render_template, request

import core
from routes.academic import bp as academic_bp
from routes.auth import bp as auth_bp
from routes.exams import bp as exams_bp
from routes.results import bp as results_bp
from routes.system import bp as system_bp

PUBLIC_ENDPOINTS = {"auth.login", "static"}


def create_app():
    app = Flask(__name__)
    os.makedirs(core.DATA_DIR, exist_ok=True)
    core.init_db()
    app.config["SECRET_KEY"] = core.secret_key()
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload ceiling
    app.config["TMP_DIR"] = core.TMP_DIR
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    app.register_blueprint(auth_bp)
    app.register_blueprint(academic_bp)
    app.register_blueprint(exams_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(system_bp)

    app.teardown_appcontext(core.close_db)

    @app.before_request
    def _prep():
        core.load_user()
        if g.user:
            core.auto_backup_if_due()
            if request.endpoint not in ("auth.password", "auth.logout", "static") and g.user["must_change_password"]:
                from flask import redirect, url_for
                return redirect(url_for("auth.password"))

    @app.context_processor
    def _inject():
        return dict(user=g.get("user"), has_perm=core.has_perm, is_admin=core.is_admin,
                    csrf_token=core.csrf_token, school=core.settings() if g.get("user") else None,
                    ROLES=core.ROLES, app_version=core.APP_VERSION)

    @app.before_request
    def _csrf():
        if request.method == "POST" and request.endpoint != "auth.login":
            core.check_csrf()

    @app.errorhandler(403)
    def _403(e):
        return render_template("error.html", code=403,
                               message="You don't have permission to view this page."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("error.html", code=404, message="That page could not be found."), 404

    @app.errorhandler(400)
    def _400(e):
        return render_template("error.html", code=400,
                               message=getattr(e, "description", None) or "That request could not be understood."), 400

    @app.errorhandler(413)
    def _413(e):
        return render_template("error.html", code=413,
                               message="That file is too large to upload (limit 25 MB)."), 413

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    is_cloud = bool(os.environ.get("PORT"))
    host = os.environ.get("HOST", "0.0.0.0" if is_cloud else "127.0.0.1")
    if not is_cloud and not os.environ.get("WERKZEUG_RUN_MAIN"):
        Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host=host, port=port, debug=False)
