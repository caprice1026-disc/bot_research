"""WSGI for Cloud Run IAM-authenticated Scheduler. No exchange private keys in shadow mode."""
from datetime import datetime
import json
import logging
import os
import time
from .config import Config
from .market import Market
from .policy import Gemini
from .reporting import notify
from .runner import Runner
from .store import FirestoreStore, Conflict


def make_app(runner, *, notifier=None):
    def application(environ, start_response):
        code, result = "200 OK", {}
        path, method = environ.get("PATH_INFO"), environ.get("REQUEST_METHOD")
        if path == "/health" and method == "GET":
            result = {"status": "ok"}
        elif path == "/tick" and method == "POST":
            try:
                # Cloud Run IAM validates OIDC; the schedule header alone is NOT authentication.
                raw = environ.get("HTTP_X_CLOUDSCHEDULER_SCHEDULETIME", "")
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
                result = runner.run(parsed.timestamp())
                if notifier:
                    try:
                        notifier()
                    except Exception:
                        logging.warning("notification_failed")
                if result["status"] == "error":
                    code = "503 Service Unavailable"
            except Conflict:
                code, result = "409 Conflict", {"status": "busy_or_conflict"}
            except (ValueError, TypeError):
                code, result = "400 Bad Request", {"status": "invalid_schedule"}
            except Exception:
                logging.exception("cycle_failed")
                code, result = "503 Service Unavailable", {"status": "unavailable"}
        else:
            code, result = "404 Not Found", {"status": "not_found"}
        body = json.dumps(result).encode()
        start_response(code, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]
    return application


def create_app():
    cfg = Config.from_env()
    store = FirestoreStore(os.environ["GOOGLE_CLOUD_PROJECT"], cfg.bot_id)
    runner = Runner(cfg, store, Market(), Gemini(cfg, store, api_key=os.environ["GEMINI_API_KEY"]))
    return make_app(runner, notifier=lambda: notify(store, os.environ.get("DISCORD_WEBHOOK_URL"), time.time(), cfg))
