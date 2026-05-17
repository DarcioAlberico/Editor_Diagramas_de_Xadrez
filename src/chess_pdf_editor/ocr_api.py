from __future__ import annotations

from typing import Iterable, Optional

import requests

from .fen import extract_piece_placement
from .types import OcrBoardResult, OcrPrediction

DEFAULT_ENDPOINTS = (
    "https://helpman.komtera.lt/chessocr/predict",
    "https://chessocr.komtera.lt/predict",
)


class OcrApiError(RuntimeError):
    pass


class OcrApiClient:
    def __init__(
        self,
        endpoint: Optional[str] = None,
        fallback_endpoints: Optional[Iterable[str]] = None,
        timeout_sec: float = 30.0,
    ) -> None:
        if endpoint:
            self.endpoints = [endpoint]
        else:
            self.endpoints = list(fallback_endpoints or DEFAULT_ENDPOINTS)
        self.timeout_sec = timeout_sec

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
                errors.append(f"{endpoint}: {exc}")
                continue

            if 300 <= response.status_code < 400:
                errors.append(f"{endpoint}: redirecionamento HTTP {response.status_code}")
                continue

            if response.status_code != 200:
                text = (response.text or "").strip()
                short_text = text[:300] if text else ""
                errors.append(f"{endpoint}: HTTP {response.status_code} {short_text}")
                continue

            try:
                data = response.json()
            except ValueError as exc:
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
                        confidence=None,
                    )
                )

            return OcrPrediction(
                request_id=str(data.get("id", "")),
                status=int(data.get("status", 0)),
                message=data.get("message"),
                results=results,
            )

        joined = " | ".join(errors) if errors else "Falha desconhecida no OCR."
        raise OcrApiError(joined)

