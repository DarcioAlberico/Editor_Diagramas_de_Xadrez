from __future__ import annotations

import os
from typing import Iterable, Optional

import requests

from .fen import extract_piece_placement
from .logging_config import get_logger
from .types import OcrBoardResult, OcrPrediction

logger = get_logger("ocr")

# Ponto unico de verdade do endpoint (§22.4): antes o valor estava duplicado
# aqui e no `QLineEdit` do app.py, entao mudar o padrao exigia editar os dois.
DEFAULT_ENDPOINTS = (
    "https://helpman.komtera.lt/chessocr/predict",
    "https://chessocr.komtera.lt/predict",
)

ENDPOINT_ENV_VAR = "CHESS_OCR_ENDPOINT"
TIMEOUT_ENV_VAR = "CHESS_OCR_TIMEOUT"
DEFAULT_TIMEOUT_SEC = 30.0

# Chaves de confianca ja vistas em respostas do servico. A API atual pode nao
# mandar nenhuma delas — nesse caso o campo continua None, que e honesto.
_CONFIDENCE_KEYS = ("confidence", "conf", "score", "probability", "prob")


def default_endpoints() -> tuple[str, ...]:
    """Cadeia de endpoints padrao, com o override de ambiente na frente."""
    env_endpoint = os.getenv(ENDPOINT_ENV_VAR, "").strip()
    if not env_endpoint:
        return DEFAULT_ENDPOINTS
    return (env_endpoint, *(e for e in DEFAULT_ENDPOINTS if e != env_endpoint))


def default_endpoint() -> str:
    """Endpoint a exibir/usar quando o usuario nao escolheu nenhum."""
    return default_endpoints()[0]


def default_timeout_sec() -> float:
    raw = os.getenv(TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s invalido (%r); usando %.0fs", TIMEOUT_ENV_VAR, raw, DEFAULT_TIMEOUT_SEC)
        return DEFAULT_TIMEOUT_SEC
    return value if value > 0 else DEFAULT_TIMEOUT_SEC


def _parse_confidence(item: dict) -> Optional[float]:
    """Le a confianca da deteccao, aceitando os nomes usuais do campo.

    Sem isso o `confidence` que atravessa `OverlayOperation` e o projeto salvo
    seria sempre nulo, e o relatorio de auditoria nasceria vazio (§22.4).
    """
    for key in _CONFIDENCE_KEYS:
        if key not in item:
            continue
        raw = item[key]
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        # Alguns servicos devolvem porcentagem (0-100) em vez de 0-1.
        if 1.0 < value <= 100.0:
            value /= 100.0
        if 0.0 <= value <= 1.0:
            return value
    return None


class OcrApiError(RuntimeError):
    pass


class OcrApiClient:
    def __init__(
        self,
        endpoint: Optional[str] = None,
        fallback_endpoints: Optional[Iterable[str]] = None,
        timeout_sec: Optional[float] = None,
    ) -> None:
        if endpoint:
            self.endpoints = [endpoint]
        else:
            self.endpoints = list(fallback_endpoints or default_endpoints())
        self.timeout_sec = default_timeout_sec() if timeout_sec is None else float(timeout_sec)

    def predict(self, image_bytes: bytes, filename: str = "board.png") -> OcrPrediction:
        if not image_bytes:
            raise OcrApiError("Imagem vazia para OCR.")

        errors: list[str] = []
        for endpoint in self.endpoints:
            try:
                response = requests.post(
                    endpoint,
                    files={"file": (filename, image_bytes, "image/png")},
                    timeout=self.timeout_sec,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                logger.warning("OCR indisponivel em %s: %s", endpoint, exc)
                errors.append(f"{endpoint}: {exc}")
                continue

            if 300 <= response.status_code < 400:
                errors.append(f"{endpoint}: redirecionamento HTTP {response.status_code}")
                continue

            if response.status_code != 200:
                text = (response.text or "").strip()
                short_text = text[:300] if text else ""
                logger.warning("OCR HTTP %s em %s: %s", response.status_code, endpoint, short_text)
                errors.append(f"{endpoint}: HTTP {response.status_code} {short_text}")
                continue

            try:
                data = response.json()
            except ValueError as exc:
                logger.warning("OCR devolveu resposta nao-JSON em %s: %s", endpoint, exc)
                errors.append(f"{endpoint}: resposta nao-JSON ({exc})")
                continue

            results: list[OcrBoardResult] = []
            for item in data.get("results", []):
                fen = extract_piece_placement(str(item.get("fen", "")).strip())
                results.append(
                    OcrBoardResult(
                        fen=fen,
                        xc=float(item.get("xc", 0.5)),
                        yc=float(item.get("yc", 0.5)),
                        width=float(item.get("width", 1.0)),
                        height=float(item.get("height", 1.0)),
                        confidence=_parse_confidence(item),
                    )
                )

            logger.info("OCR ok em %s: %d tabuleiro(s) em %s", endpoint, len(results), filename)
            return OcrPrediction(
                request_id=str(data.get("id", "")),
                status=int(data.get("status", 0)),
                message=data.get("message"),
                results=results,
            )

        joined = " | ".join(errors) if errors else "Falha desconhecida no OCR."
        logger.error("OCR falhou em todos os endpoints: %s", joined)
        raise OcrApiError(joined)
