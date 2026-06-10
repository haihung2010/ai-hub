"""Unit tests for /v1/audio/transcriptions (P0.4 — upload size cap, 2026-06-10)."""
from __future__ import annotations

import io

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# 25 MB cap
# ──────────────────────────────────────────────────────────────────────


class _FakeWhisper:
    """Minimal stand-in for WhisperService — no model, no GPU."""

    def transcribe(self, audio, language=None):  # noqa: ARG002
        # Read just enough to confirm the body was handed in.
        audio.read(1)
        return {
            "text": "transcribed",
            "language": "vi",
            "language_probability": 0.99,
        }


@pytest.fixture
def client_with_whisper(client, monkeypatch):
    """Attach a fake Whisper service to app.state."""
    # Find the actual app instance from the test client
    fake = _FakeWhisper()
    # Stash on the test app's state via the test client's app
    client.app.state.whisper_service = fake
    return client


def test_audio_accepts_small_upload(client_with_whisper) -> None:
    """A small (well under 25 MB) audio upload should succeed."""
    fake_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00FAKE_MP3" * 100
    resp = client_with_whisper.post(
        "/v1/audio/transcriptions",
        files={"file": ("test.mp3", io.BytesIO(fake_bytes), "audio/mpeg")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["text"] == "transcribed"
    assert data["language"] == "vi"
    assert data["bytes"] == len(fake_bytes)


def test_audio_rejects_oversized_upload(client_with_whisper) -> None:
    """An upload > 25 MB must be rejected with 413 BEFORE Whisper is called."""
    # Build a 26 MB payload
    big = b"\x00" * (26 * 1024 * 1024)
    resp = client_with_whisper.post(
        "/v1/audio/transcriptions",
        files={"file": ("huge.wav", io.BytesIO(big), "audio/wav")},
    )
    assert resp.status_code == 413
    assert "25 MB" in resp.json()["detail"] or "limit" in resp.json()["detail"].lower()


def test_audio_rejects_empty_upload(client_with_whisper) -> None:
    """Zero-byte upload is a 400 (caller error), not a 500."""
    resp = client_with_whisper.post(
        "/v1/audio/transcriptions",
        files={"file": ("empty.mp3", io.BytesIO(b""), "audio/mpeg")},
    )
    assert resp.status_code == 400


def test_audio_503_when_whisper_unavailable(client) -> None:
    """If the Whisper service wasn't started, return 503 not 500."""
    client.app.state.whisper_service = None
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("x.mp3", io.BytesIO(b"data"), "audio/mpeg")},
    )
    assert resp.status_code == 503
    assert "Whisper" in resp.json()["detail"]


def test_audio_requires_api_key(client_with_whisper) -> None:
    """X-API-KEY is enforced by the security middleware."""
    # Strip the auto-attached API key header
    bare = client_with_whisper
    bare.headers = type(bare.headers)()  # empty
    resp = bare.post(
        "/v1/audio/transcriptions",
        files={"file": ("x.mp3", io.BytesIO(b"data"), "audio/mpeg")},
    )
    # Either 401 (auth) or 403 (forbidden) is acceptable; never 200
    assert resp.status_code in (401, 403), resp.text


def test_audio_passes_language_param(client_with_whisper) -> None:
    """The `language` form field is forwarded to the Whisper service."""
    captured = {}

    class _SpyWhisper(_FakeWhisper):
        def transcribe(self, audio, language=None):
            captured["language"] = language
            return super().transcribe(audio, language)

    client_with_whisper.app.state.whisper_service = _SpyWhisper()
    resp = client_with_whisper.post(
        "/v1/audio/transcriptions",
        files={"file": ("x.mp3", io.BytesIO(b"data"), "audio/mpeg")},
        data={"language": "vi"},
    )
    assert resp.status_code == 200
    assert captured["language"] == "vi"
