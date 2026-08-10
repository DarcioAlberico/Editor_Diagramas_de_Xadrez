"""Configuracao do OCR e leitura da confianca (§22.4), sem tocar na rede."""
from __future__ import annotations

import pytest

from chess_pdf_editor import ocr_api
from chess_pdf_editor.ocr_api import (
    DEFAULT_ENDPOINTS,
    OcrApiClient,
    OcrApiError,
    default_endpoint,
    default_endpoints,
    default_timeout_sec,
)


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(ocr_api.ENDPOINT_ENV_VAR, raising=False)
    monkeypatch.delenv(ocr_api.TIMEOUT_ENV_VAR, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Endpoint / timeout
# ---------------------------------------------------------------------------


def test_default_endpoint_comes_from_the_single_source(clean_env) -> None:
    assert default_endpoint() == DEFAULT_ENDPOINTS[0]
    assert default_endpoints() == DEFAULT_ENDPOINTS


def test_env_var_wins_but_keeps_the_fallback_chain(clean_env) -> None:
    clean_env.setenv(ocr_api.ENDPOINT_ENV_VAR, "https://interno/predict")
    assert default_endpoint() == "https://interno/predict"
    # Os padroes continuam atras, entao um endpoint interno fora do ar nao
    # deixa o usuario sem OCR nenhum.
    assert default_endpoints()[1:] == DEFAULT_ENDPOINTS


def test_env_var_equal_to_a_default_does_not_duplicate_it(clean_env) -> None:
    clean_env.setenv(ocr_api.ENDPOINT_ENV_VAR, DEFAULT_ENDPOINTS[1])
    assert default_endpoints() == (DEFAULT_ENDPOINTS[1], DEFAULT_ENDPOINTS[0])


def test_explicit_endpoint_is_used_alone(clean_env) -> None:
    client = OcrApiClient(endpoint="https://so-esse/predict")
    assert client.endpoints == ["https://so-esse/predict"]


def test_client_without_endpoint_uses_the_chain(clean_env) -> None:
    assert OcrApiClient().endpoints == list(DEFAULT_ENDPOINTS)


def test_timeout_from_env(clean_env) -> None:
    clean_env.setenv(ocr_api.TIMEOUT_ENV_VAR, "5")
    assert default_timeout_sec() == 5.0
    assert OcrApiClient().timeout_sec == 5.0


@pytest.mark.parametrize("raw", ["abc", "0", "-3"])
def test_invalid_timeout_falls_back_to_the_default(clean_env, raw: str) -> None:
    clean_env.setenv(ocr_api.TIMEOUT_ENV_VAR, raw)
    assert default_timeout_sec() == ocr_api.DEFAULT_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# Confianca
# ---------------------------------------------------------------------------


def _predict_with(monkeypatch, item: dict):
    payload = {"id": "x", "status": 200, "results": [dict({"fen": "8/8/8/4k3/8/8/4K3/8"}, **item)]}
    monkeypatch.setattr(ocr_api.requests, "post", lambda *a, **k: _FakeResponse(payload))
    return OcrApiClient(endpoint="https://fake/predict").predict(b"png-bytes")


@pytest.mark.parametrize("key", ["confidence", "conf", "score", "probability", "prob"])
def test_confidence_is_read_from_the_usual_field_names(monkeypatch, key: str) -> None:
    prediction = _predict_with(monkeypatch, {key: 0.87})
    assert prediction.results[0].confidence == pytest.approx(0.87)


def test_percentage_confidence_is_normalized(monkeypatch) -> None:
    prediction = _predict_with(monkeypatch, {"confidence": 92.5})
    assert prediction.results[0].confidence == pytest.approx(0.925)


def test_confidence_stays_none_when_the_service_omits_it(monkeypatch) -> None:
    """Nao inventar numero: sem o campo, o valor honesto e nulo."""
    prediction = _predict_with(monkeypatch, {})
    assert prediction.results[0].confidence is None


@pytest.mark.parametrize("value", ["alto", None, True, float("nan")])
def test_garbage_confidence_is_ignored(monkeypatch, value) -> None:
    prediction = _predict_with(monkeypatch, {"confidence": value})
    assert prediction.results[0].confidence is None


def test_all_endpoints_failing_raises_with_every_reason(monkeypatch, clean_env) -> None:
    def _boom(endpoint, **kwargs):
        raise ocr_api.requests.RequestException(f"sem rota para {endpoint}")

    monkeypatch.setattr(ocr_api.requests, "post", _boom)
    with pytest.raises(OcrApiError) as excinfo:
        OcrApiClient().predict(b"png-bytes")

    message = str(excinfo.value)
    for endpoint in DEFAULT_ENDPOINTS:
        assert endpoint in message


def test_empty_image_fails_before_any_request(monkeypatch) -> None:
    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("nao deveria chamar a rede")

    monkeypatch.setattr(ocr_api.requests, "post", _should_not_be_called)
    with pytest.raises(OcrApiError):
        OcrApiClient(endpoint="https://fake/predict").predict(b"")
