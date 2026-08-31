"""Assistant service: authenticated adapters around the shared runtime."""

from flask import Flask, jsonify

from .auth import install_security
from .config import Settings, load
from .runtime import Runtime


def create_app(settings: Settings, runtime: Runtime | None = None) -> Flask:
    app = Flask(__name__)
    engine = runtime or Runtime(settings)
    app.extensions["macbot_runtime"] = engine
    install_security(app, settings, "assistant", engine.auth)

    @app.get("/health")
    def health():
        return jsonify(status="alive", service="assistant")

    @app.get("/ready")
    @app.get("/info")
    def ready():
        import os

        status = engine.status()
        native_required = bool(os.getenv("MACBOT_NATIVE_TOKEN_PATH"))
        native_ready = bool(app.extensions.get("macbot_native_ready", not native_required))
        code = 200 if native_ready else 503
        return jsonify(
            status="ready" if native_ready else "blocked",
            pid=os.getpid(),
            native_ipc=native_ready,
            **status,
        ), code

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc), code="invalid_request"), 400

    @app.errorhandler(PermissionError)
    def forbidden(exc):
        return jsonify(error=str(exc), code="denied"), 403

    @app.errorhandler(RuntimeError)
    def unavailable(exc):
        return jsonify(error=str(exc), code="unavailable"), 503

    return app


def main():
    from .service_lifecycle import termination_cleanup

    termination_cleanup()
    settings = load()
    app = create_app(settings)
    from .native_ipc import NativeIPCServer

    native = NativeIPCServer(settings, app.extensions["macbot_runtime"])
    if not native.start():
        app.extensions["macbot_runtime"].close()
        raise RuntimeError("Native IPC is required but could not start")
    app.extensions["macbot_native_ready"] = True
    try:
        app.run(
            host=settings.services.assistant.host,
            port=settings.services.assistant.port,
            threaded=True,
            use_reloader=False,
        )
    finally:
        native.close()
        app.extensions["macbot_runtime"].close()


if __name__ == "__main__":
    main()
