"""Flask test-client integration tests for neband.api.app -- the actual
HTTP surface, exercised end-to-end (multipart upload in, JSON out) rather
than calling analysis.py's pure functions directly.
"""

import io
import json

import pytest
from PIL import Image

from neband.api.app import create_app

LANE_WIDTH = 60
GAP_WIDTH = 20


def _lane_bounds(index: int) -> tuple[int, int]:
    x_start = GAP_WIDTH + index * (LANE_WIDTH + GAP_WIDTH)
    return x_start, x_start + LANE_WIDTH


def _png_bytes(image_2d) -> bytes:
    arr8 = (image_2d * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr8).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def two_lane_png(synthetic_gel):
    image = synthetic_gel(
        height=300,
        lane_width=LANE_WIDTH,
        gap_width=GAP_WIDTH,
        band_specs=[[(100.0, 0.8)], [(100.0, 0.8)]],
    )
    return _png_bytes(image)


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    return app.test_client()


def _lane_dict(index: int, is_ladder: bool = False) -> dict:
    x_start, x_end = _lane_bounds(index)
    return {"client_id": index, "x_start": x_start, "x_end": x_end, "is_ladder": is_ladder}


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_lanes_detect_happy_path(client, two_lane_png):
    resp = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "image_id" in body and len(body["image_id"]) == 64  # sha256 hex
    assert len(body["lanes"]) == 2
    assert body["lanes"][0]["is_ladder"] is True


def test_lanes_detect_same_bytes_yield_same_image_id(client, two_lane_png):
    resp1 = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    resp2 = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    assert resp1.get_json()["image_id"] == resp2.get_json()["image_id"]


def test_lanes_detect_missing_image_is_400(client):
    resp = client.post("/v1/lanes/detect", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_analyze_happy_path(client, two_lane_png):
    payload = {
        "lanes": [_lane_dict(0), _lane_dict(1)],
        "reference": {"client_id": 0, "row": 100},
    }
    resp = client.post(
        "/v1/analyze",
        data={
            "image": (io.BytesIO(two_lane_png), "gel.png"),
            "payload": json.dumps(payload),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["results"]["0"]["purity_percent"] == 100
    assert body["results"]["1"]["purity_percent"] == 100


def test_analyze_missing_payload_field_is_400(client, two_lane_png):
    resp = client.post(
        "/v1/analyze",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_analyze_malformed_json_payload_is_400(client, two_lane_png):
    resp = client.post(
        "/v1/analyze",
        data={
            "image": (io.BytesIO(two_lane_png), "gel.png"),
            "payload": "{not valid json",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_analyze_incomplete_payload_missing_reference_is_400(client, two_lane_png):
    payload = {"lanes": [_lane_dict(0), _lane_dict(1)]}  # no "reference"
    resp = client.post(
        "/v1/analyze",
        data={
            "image": (io.BytesIO(two_lane_png), "gel.png"),
            "payload": json.dumps(payload),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_analyze_bad_click_returns_400_with_message(client, two_lane_png):
    payload = {
        "lanes": [_lane_dict(0), _lane_dict(1)],
        "reference": {"client_id": 0, "row": 280},  # far from the band at row 100
    }
    resp = client.post(
        "/v1/analyze",
        data={
            "image": (io.BytesIO(two_lane_png), "gel.png"),
            "payload": json.dumps(payload),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "No detected band near your click" in resp.get_json()["error"]


def test_api_key_enforced_when_configured(two_lane_png):
    app = create_app(api_key="secret-value")
    app.testing = True
    client = app.test_client()

    no_key_resp = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    assert no_key_resp.status_code == 401

    wrong_key_resp = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
        headers={"X-Neband-Api-Key": "wrong"},
    )
    assert wrong_key_resp.status_code == 401

    right_key_resp = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
        headers={"X-Neband-Api-Key": "secret-value"},
    )
    assert right_key_resp.status_code == 200


def test_api_key_does_not_gate_healthz(two_lane_png):
    app = create_app(api_key="secret-value")
    app.testing = True
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_no_api_key_configured_means_no_auth_check(client, two_lane_png):
    # `client` fixture's app has no api_key set -- requests succeed with no header.
    resp = client.post(
        "/v1/lanes/detect",
        data={"image": (io.BytesIO(two_lane_png), "gel.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
