"""Flask app factory: the JSON-only HTTP surface for the review widget.

Two routes, both accepting the image as a multipart file part on every
call (not a server-side "currently loaded image" like the disposable
prototype) -- this keeps each request self-contained: a cache miss or
eviction just means one extra decode, never a broken session (see
`image_cache.py`).

- `POST /v1/lanes/detect` -- auto-detect initial lane boxes for a
  freshly-uploaded image.
- `POST /v1/analyze` -- the human-in-the-loop analysis itself, given the
  caller's current lane layout and reference click.

Auth (GH issue #1, Fix #3): this app has no built-in end-user auth. The
primary boundary is meant to be network-level -- bind to localhost/a Unix
socket, or put a reverse proxy in front that's the only thing allowed to
reach it. An optional shared-secret header check (`X-Neband-Api-Key`) is
available as defense-in-depth on top of that, not instead of it -- set via
`create_app(api_key=...)` or the `NEBAND_API_KEY` environment variable; if
neither is set, no header check is performed at all.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request

from neband.api.analysis import AnalyzeError, run_analyze, run_lane_detect
from neband.api.image_cache import ImageCache, content_id
from neband.core.image_io import load_image, to_signal
from neband.purity.analysis import _resolve_known_mws

DEFAULT_MAX_UPLOAD_BYTES = 50_000_000  # 50 MB


def _decode_and_cache(image_bytes: bytes, filename: str | None, cache: ImageCache):
    """(image_id, signal) for `image_bytes` -- decodes and caches on a miss.

    Writes to a temp file rather than changing `load_image` to accept raw
    bytes: `load_image` is a tested core function used elsewhere unchanged,
    and `skimage.io.imread` needs a real extension to pick the right
    decoder (this matters most for 16-bit TIFF -- see `core/image_io.py`).
    The original upload's filename supplies that extension when available.
    """
    image_id = content_id(image_bytes)
    cached = cache.get(image_id)
    if cached is not None:
        return image_id, cached

    suffix = Path(filename).suffix if filename else ""
    if not suffix:
        suffix = ".tif"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(image_bytes)
        tmp.flush()
        raw = load_image(tmp.name)
    signal = to_signal(raw)
    cache.put(image_id, signal)
    return image_id, signal


def _require_image_file():
    """The uploaded `image` part's bytes + original filename, or an
    `AnalyzeError`-style (message, status) pair signaling a 400.
    """
    if "image" not in request.files:
        return (None, None), "Missing required 'image' file part."
    file_storage = request.files["image"]
    image_bytes = file_storage.read()
    if not image_bytes:
        return (None, None), "Uploaded 'image' file is empty."
    return (image_bytes, file_storage.filename), None


def create_app(
    max_cache_entries: int | None = None,
    max_cache_bytes: int | None = None,
    api_key: str | None = None,
    max_upload_bytes: int | None = None,
) -> Flask:
    app = Flask(__name__)

    cache_kwargs = {}
    if max_cache_entries is not None:
        cache_kwargs["max_entries"] = max_cache_entries
    if max_cache_bytes is not None:
        cache_kwargs["max_bytes"] = max_cache_bytes
    app.config["IMAGE_CACHE"] = ImageCache(**cache_kwargs)

    app.config["API_KEY"] = api_key if api_key is not None else os.environ.get("NEBAND_API_KEY")
    app.config["MAX_CONTENT_LENGTH"] = (
        max_upload_bytes
        if max_upload_bytes is not None
        else int(os.environ.get("NEBAND_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))
    )

    @app.before_request
    def _check_api_key():
        if request.path == "/healthz":
            return None
        configured = app.config["API_KEY"]
        if configured and request.headers.get("X-Neband-Api-Key") != configured:
            return jsonify({"error": "Missing or invalid X-Neband-Api-Key header."}), 401
        return None

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.post("/v1/lanes/detect")
    def lanes_detect():
        (image_bytes, filename), error = _require_image_file()
        if error:
            return jsonify({"error": error}), 400

        image_id, signal = _decode_and_cache(image_bytes, filename, app.config["IMAGE_CACHE"])
        result = run_lane_detect(signal)
        return jsonify(
            {
                "image_id": image_id,
                "width": result.width,
                "height": result.height,
                "lanes": result.lanes,
            }
        )

    @app.post("/v1/analyze")
    def analyze():
        (image_bytes, filename), error = _require_image_file()
        if error:
            return jsonify({"error": error}), 400

        payload_raw = request.form.get("payload")
        if payload_raw is None:
            return jsonify({"error": "Missing required 'payload' form field."}), 400
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"'payload' is not valid JSON: {exc}"}), 400

        try:
            lanes = payload["lanes"]
            reference = payload["reference"]
            excluded_from_series = set(payload.get("excluded_from_series", []))
            deleted_ranges_by_lane = {
                int(client_id_str): {(band["y_start"], band["y_end"]) for band in ranges}
                for client_id_str, ranges in payload.get("deleted_bands", {}).items()
            }
            ladder_bands = payload.get("ladder_bands")
            if ladder_bands is not None:
                ladder_bands = [float(v) for v in ladder_bands]
            known_mws = _resolve_known_mws(payload.get("ladder"), ladder_bands)
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": f"Malformed 'payload': {exc}"}), 400

        image_id, signal = _decode_and_cache(image_bytes, filename, app.config["IMAGE_CACHE"])
        try:
            results = run_analyze(
                signal=signal,
                lanes=lanes,
                reference=reference,
                excluded_from_series=excluded_from_series,
                deleted_ranges_by_lane=deleted_ranges_by_lane,
                known_mws=known_mws,
            )
        except AnalyzeError as exc:
            return jsonify({"error": str(exc)}), 400
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": f"Malformed 'payload': {exc}"}), 400

        return jsonify({"image_id": image_id, "results": results})

    return app
