"""Escolha e composição dos motores de reconhecimento (Sprint 7.2).

Tudo aqui roda com dublês: nenhum teste toca a rede nem carrega modelo. O que está
sendo protegido é a *política* — quando o remoto é chamado, o que ele substitui e o
que acontece quando um dos dois falha.
"""
from __future__ import annotations

import pytest

from chess_pdf_editor.recognition import (
    DEFAULT_ENGINE_MODE,
    ENGINE_HYBRID,
    ENGINE_LOCAL,
    ENGINE_REMOTE,
    HybridEngine,
    RecognitionError,
    RemoteEngine,
    make_engine,
    mode_uses_network,
    normalize_mode,
)
from chess_pdf_editor.types import OcrBoardResult, OcrPrediction


def _board(fen: str, confidence, xc=0.5, yc=0.5, size=0.4) -> OcrBoardResult:
    return OcrBoardResult(
        fen=fen, xc=xc, yc=yc, width=size, height=size, confidence=confidence
    )


def _prediction(*results: OcrBoardResult, request_id="fake") -> OcrPrediction:
    return OcrPrediction(
        request_id=request_id, status=200, message=None, results=list(results)
    )


class _FakeEngine:
    """Motor programável. `calls` prova se ele chegou a ser chamado."""

    def __init__(self, prediction=None, error: str = "", network: bool = False) -> None:
        self._prediction = prediction
        self._error = error
        self._network = network
        self.calls: list[str] = []

    name = "fake"

    def uses_network(self) -> bool:
        return self._network

    def predict(self, image_png, filename="board.png", assume_whole_image=False):
        self.calls.append(filename)
        if self._error:
            raise RecognitionError(self._error)
        return self._prediction


CONFIDENT = _board("8/8/8/4k3/8/8/4K3/8", 0.97)
UNSURE = _board("8/8/8/4k3/8/8/4K3/8", 0.41)


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------


def test_the_default_mode_is_hybrid() -> None:
    assert DEFAULT_ENGINE_MODE == ENGINE_HYBRID
    assert normalize_mode(None) == ENGINE_HYBRID
    assert normalize_mode("nao-existe") == ENGINE_HYBRID


def test_only_the_local_mode_promises_no_network() -> None:
    """É essa promessa que decide se o aviso de privacidade aparece."""
    assert mode_uses_network(ENGINE_LOCAL) is False
    assert mode_uses_network(ENGINE_REMOTE) is True
    assert mode_uses_network(ENGINE_HYBRID) is True


def test_the_remote_mode_never_needs_the_local_engine() -> None:
    engine = make_engine(ENGINE_REMOTE, endpoint="https://exemplo/predict")
    assert isinstance(engine, RemoteEngine)


def test_hybrid_falls_back_to_remote_when_the_local_engine_is_missing(monkeypatch) -> None:
    """Padrão de fábrica não pode quebrar numa instalação sem torch."""
    from chess_pdf_editor import recognition

    def _no_local(*args, **kwargs):
        raise RecognitionError("sem torch")

    monkeypatch.setattr(recognition, "LocalEngine", _no_local)
    assert isinstance(make_engine(ENGINE_HYBRID), RemoteEngine)


def test_the_local_mode_refuses_instead_of_silently_using_the_network(monkeypatch) -> None:
    """Quem pediu offline não pode ganhar rede por baixo do pano."""
    from chess_pdf_editor import recognition

    def _no_local(*args, **kwargs):
        raise RecognitionError("sem torch")

    monkeypatch.setattr(recognition, "LocalEngine", _no_local)
    with pytest.raises(RecognitionError):
        make_engine(ENGINE_LOCAL)


# ---------------------------------------------------------------------------
# Híbrido
# ---------------------------------------------------------------------------


def test_a_confident_local_read_never_touches_the_network() -> None:
    local = _FakeEngine(_prediction(CONFIDENT))
    remote = _FakeEngine(_prediction(_board("8/8/8/8/8/8/8/8", 0.9)), network=True)

    prediction = HybridEngine(local, remote).predict(b"png", filename="p1.png")

    assert remote.calls == [], "o remoto foi chamado sem necessidade"
    assert prediction.results == [CONFIDENT]


def test_an_unsure_local_read_is_replaced_by_the_remote_one() -> None:
    local = _FakeEngine(_prediction(UNSURE))
    remote_board = _board("8/8/8/3qk3/8/8/4K3/8", 0.88)
    remote = _FakeEngine(_prediction(remote_board), network=True)

    prediction = HybridEngine(local, remote).predict(b"png")

    assert remote.calls, "com confiança 0,41 o reforço tinha de acontecer"
    (result,) = prediction.results
    assert result.fen == remote_board.fen
    assert result.confidence == pytest.approx(0.88)


def test_the_reinforced_board_keeps_the_local_geometry() -> None:
    """O detector local é exato em pixel; a caixa do remoto vem arredondada."""
    local = _FakeEngine(_prediction(_board("8/8/8/4k3/8/8/4K3/8", 0.30, xc=0.31, size=0.22)))
    remote = _FakeEngine(
        _prediction(_board("8/8/8/3qk3/8/8/4K3/8", 0.9, xc=0.33, size=0.25)), network=True
    )

    (result,) = HybridEngine(local, remote).predict(b"png").results

    assert (result.xc, result.width) == pytest.approx((0.31, 0.22))
    assert result.fen == "8/8/8/3qk3/8/8/4K3/8"


def test_only_the_unsure_boards_are_replaced() -> None:
    sure = _board("8/8/8/4k3/8/8/4K3/8", 0.99, xc=0.25)
    unsure = _board("8/8/8/4k3/8/8/4K3/8", 0.20, xc=0.75)
    local = _FakeEngine(_prediction(sure, unsure))
    remote = _FakeEngine(
        _prediction(
            _board("QQQQQQQQ/8/8/8/8/8/8/8", 0.9, xc=0.25),
            _board("8/8/8/3qk3/8/8/4K3/8", 0.9, xc=0.75),
        ),
        network=True,
    )

    results = HybridEngine(local, remote).predict(b"png").results

    assert results[0].fen == sure.fen, "tabuleiro confiante não podia ser tocado"
    assert results[1].fen == "8/8/8/3qk3/8/8/4K3/8"


def test_a_board_only_the_remote_saw_is_added() -> None:
    local = _FakeEngine(_prediction(_board("8/8/8/4k3/8/8/4K3/8", 0.10, xc=0.25, size=0.2)))
    extra = _board("8/8/8/3qk3/8/8/4K3/8", 0.95, xc=0.80, size=0.2)
    remote = _FakeEngine(_prediction(extra), network=True)

    results = HybridEngine(local, remote).predict(b"png").results

    assert len(results) == 2
    assert any(result.xc == pytest.approx(0.80) for result in results)


def test_a_missing_confidence_counts_as_unsure() -> None:
    """Não saber a confiança não é o mesmo que ter confiança alta."""
    local = _FakeEngine(_prediction(_board("8/8/8/4k3/8/8/4K3/8", None)))
    remote = _FakeEngine(_prediction(_board("8/8/8/3qk3/8/8/4K3/8", 0.9)), network=True)

    HybridEngine(local, remote).predict(b"png")
    assert remote.calls


def test_an_empty_local_read_asks_the_remote() -> None:
    local = _FakeEngine(_prediction())
    remote = _FakeEngine(_prediction(CONFIDENT), network=True)

    prediction = HybridEngine(local, remote).predict(b"png")

    assert remote.calls
    assert prediction.results == [CONFIDENT]


def test_a_network_failure_keeps_the_local_read() -> None:
    """Sem rede, a leitura insegura ainda é a melhor resposta que existe."""
    local = _FakeEngine(_prediction(UNSURE))
    remote = _FakeEngine(error="sem internet", network=True)

    prediction = HybridEngine(local, remote).predict(b"png")

    assert prediction.results == [UNSURE]


def test_a_local_failure_falls_back_to_the_remote() -> None:
    local = _FakeEngine(error="modelo corrompido")
    remote = _FakeEngine(_prediction(CONFIDENT), network=True)

    prediction = HybridEngine(local, remote).predict(b"png")

    assert prediction.results == [CONFIDENT]


def test_both_failing_raises() -> None:
    local = _FakeEngine(error="modelo corrompido")
    remote = _FakeEngine(error="sem internet", network=True)

    with pytest.raises(RecognitionError):
        HybridEngine(local, remote).predict(b"png")


def test_hybrid_declares_network_use_even_when_it_rarely_happens() -> None:
    """O aviso de privacidade descreve o que *pode* acontecer, não a média."""
    engine = HybridEngine(_FakeEngine(_prediction(CONFIDENT)), _FakeEngine(network=True))
    assert engine.uses_network() is True


def test_the_whole_image_hint_reaches_the_local_engine() -> None:
    """`Reconhecer seleção` depende disso quando o recorte não tem moldura."""

    class _Recording(_FakeEngine):
        def __init__(self) -> None:
            super().__init__(_prediction(CONFIDENT))
            self.hints: list[bool] = []

        def predict(self, image_png, filename="board.png", assume_whole_image=False):
            self.hints.append(assume_whole_image)
            return super().predict(image_png, filename, assume_whole_image)

    local = _Recording()
    HybridEngine(local, _FakeEngine(network=True)).predict(b"png", assume_whole_image=True)
    assert local.hints == [True]
