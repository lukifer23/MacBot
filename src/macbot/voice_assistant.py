"""Assistant service: authenticated adapters around the shared runtime."""

from flask import Flask, jsonify, request

from .auth import install_security
from .config import Settings, load
from .runtime import Runtime
from .validation import decode_audio, json_object


def create_app(settings: Settings, runtime: Runtime | None = None) -> Flask:
    app = Flask(__name__)
    engine = runtime or Runtime(settings)
    app.extensions["macbot_runtime"] = engine
    install_security(app, settings, "assistant", engine.auth)

    def session_id():
        value = request.headers.get("X-MacBot-Session", "local")
        if len(value) > 128 or not value:
            raise ValueError("Invalid session")
        return value

    @app.get("/health")
    def health():
        return jsonify(status="alive", service="assistant")

    @app.get("/ready")
    @app.get("/info")
    def ready():
        import os

        return jsonify(status="ready", pid=os.getpid(), **engine.status())

    @app.get("/events")
    def events():
        after = max(0, int(request.args.get("after", "0")))
        return jsonify(engine.events.read(after, timeout=20, epoch=request.args.get("epoch")))

    @app.get("/audio-status")
    def audio_status():
        return jsonify(engine.audio_status())

    @app.post("/chat")
    def chat():
        data = json_object()
        turn = engine.submit(
            data.get("message"), speak=data.get("speak", True), session_id=session_id()
        )
        return jsonify(success=True, state="accepted", turn_id=turn.id), 202

    @app.post("/voice")
    def voice():
        engine.listen(False)
        content, suffix = decode_audio(json_object().get("audio"))
        if not engine.transcriber:
            raise RuntimeError("STT is not loaded")
        samples = engine.transcriber.decode(content, suffix)
        engine.browser_recording(False, session_id())
        turn = engine.submit(audio=samples, session_id=session_id())
        return jsonify(success=True, state="accepted", turn_id=turn.id), 202

    @app.post("/browser-recording")
    def browser_recording():
        enabled = json_object().get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        engine.browser_recording(enabled, session_id())
        return jsonify(success=True, max_duration_ms=settings.audio.max_utterance_sec * 1000)

    @app.post("/interrupt")
    def interrupt():
        engine.interrupt()
        return jsonify(success=True, state="interrupted")

    @app.post("/listen")
    def listen():
        enabled = json_object().get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        engine.listen(enabled, session_id=session_id())
        return jsonify(success=True, **engine.audio_status())

    @app.post("/approve")
    def approve():
        data = json_object()
        if not isinstance(data.get("approve"), bool):
            raise ValueError("approve must be boolean")
        result = engine.decide(
            data.get("action_id", ""),
            data.get("turn_id", ""),
            data["approve"],
            session_id=session_id(),
        )
        return jsonify(success=True, result=result)

    @app.post("/clear")
    def clear():
        engine.clear()
        return jsonify(success=True)

    @app.post("/speak")
    def speak():
        # A preview uses actual synthesis/playback and is tracked as a turn, not an LLM reply.
        if not engine.synth:
            raise RuntimeError("TTS is not loaded")
        turn = engine.submit(
            json_object().get("text"), speak=True, synthesis_only=True, session_id=session_id()
        )
        return jsonify(success=True, state="accepted", turn_id=turn.id), 202

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
    try:
        app.run(
            host=settings.services.assistant.host,
            port=settings.services.assistant.port,
            threaded=True,
            use_reloader=False,
        )
    finally:
        app.extensions["macbot_runtime"].close()


if __name__ == "__main__":
    main()
