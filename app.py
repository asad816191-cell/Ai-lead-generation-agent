"""Flask application: dashboard, JSON API and exports."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from flask import Flask, abort, jsonify, render_template, request, send_file

from config import Settings
from database import Database
from services.export_service import export_csv, export_xlsx
from services.models import LEAD_FIELDS
from services.pipeline import build_llm, build_providers, run_search
from services.prompt_parser import parse_prompt
from services.validation import ValidationError, validate_search_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, provider_factory=None, enricher=None) -> Flask:
    """`provider_factory(settings, llm)` and `enricher` let tests inject fakes."""
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.config["SETTINGS"] = settings

    db = Database(settings.DATABASE_PATH)
    db.mark_interrupted()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search")
    app.extensions["lead_db"] = db
    app.extensions["lead_executor"] = executor

    columns = [{"key": k, "label": label} for k, label in LEAD_FIELDS]

    # ---------------------------------------------------------------- security headers
    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        return resp

    # ---------------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return render_template("index.html", columns=columns, settings=settings)

    @app.get("/history")
    def history():
        return render_template("history.html", searches=db.list_searches(200))

    # ------------------------------------------------------------------------ API
    @app.get("/api/status")
    def api_status():
        # Only booleans - the keys themselves never leave the server.
        return jsonify({
            "google_places": bool(settings.GOOGLE_PLACES_API_KEY),
            "tavily": bool(settings.TAVILY_API_KEY),
            "openai": bool(settings.OPENAI_API_KEY),
            "max_leads": settings.MAX_LEADS_PER_SEARCH,
            "default_count": settings.DEFAULT_LEAD_COUNT,
        })

    @app.post("/api/search")
    def api_search():
        if not request.is_json:
            return jsonify({"error": "Send the request as JSON."}), 415
        try:
            payload = validate_search_payload(request.get_json(silent=True), settings.MAX_LEADS_PER_SEARCH)
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400

        llm = build_llm(settings)
        prompt = payload["prompt"]
        if not prompt:
            prompt = f"Find {payload['count'] or settings.DEFAULT_LEAD_COUNT} {payload['industry']} in {payload['location']}"
        try:
            spec = parse_prompt(prompt, llm=llm, default_count=settings.DEFAULT_LEAD_COUNT,
                                max_leads=settings.MAX_LEADS_PER_SEARCH,
                                overrides={"industry": payload["industry"], "location": payload["location"],
                                           "count": payload["count"]})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        search_id = db.create_search(prompt, spec.industry, spec.location, spec.count,
                                     spec.filters, payload["enrich"])
        providers = provider_factory(settings, llm) if provider_factory else None
        executor.submit(run_search, search_id, spec, settings, db, enrich=payload["enrich"],
                        providers=providers, enricher=enricher, llm=llm)
        return jsonify({"id": search_id, "industry": spec.industry, "location": spec.location,
                        "count": spec.count, "filters": spec.filters, "parser": spec.parser}), 202

    @app.get("/api/search/<int:search_id>")
    def api_get_search(search_id: int):
        search = db.get_search(search_id)
        if not search:
            return jsonify({"error": "Search not found."}), 404
        leads = db.get_leads(search_id) if search["status"] == "completed" else []
        return jsonify({"search": search, "leads": leads})

    @app.get("/api/searches")
    def api_list_searches():
        return jsonify({"searches": db.list_searches(int(request.args.get("limit", 20)) if
                                                      request.args.get("limit", "20").isdigit() else 20)})

    @app.delete("/api/search/<int:search_id>")
    def api_delete_search(search_id: int):
        if not db.delete_search(search_id):
            return jsonify({"error": "Search not found."}), 404
        return jsonify({"deleted": search_id})

    @app.get("/api/search/<int:search_id>/export/<fmt>")
    def api_export(search_id: int, fmt: str):
        if fmt not in ("xlsx", "csv"):
            abort(404)
        search = db.get_search(search_id)
        if not search:
            abort(404)
        leads = db.get_leads(search_id)
        slug = re.sub(r"[^a-z0-9]+", "_", f"{search['industry']}_{search['location']}".lower()).strip("_")[:50]
        filename = f"leads_{slug}_{search_id}.{fmt}"
        if fmt == "csv":
            data, mime = export_csv(leads), "text/csv"
        else:
            data = export_xlsx(leads, {"prompt": search["prompt"]})
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return send_file(BytesIO(data), mimetype=mime, as_attachment=True, download_name=filename)

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return "Not found", 404

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "Request too large."}), 413

    return app


if __name__ == "__main__":
    _settings = Settings.from_env()
    _app = create_app(_settings)
    print(f"\n  AI Lead Generation Agent running at http://{_settings.HOST}:{_settings.PORT}\n")
    _app.run(host=_settings.HOST, port=_settings.PORT, debug=False, threaded=True)
