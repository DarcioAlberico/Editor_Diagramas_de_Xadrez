"""Reconhecimento local de diagramas (Sprint 7).

Antes deste pacote o produto tinha uma dependência dura de rede: `Detectar no PDF`
mandava **todas** as páginas do livro para `helpman.komtera.lt`. Um livro de 898
páginas eram 898 requisições HTTP, e sem internet o reconhecimento simplesmente não
existia.

Aqui o mesmo trabalho acontece na máquina, com o detector por contorno e o
classificador CNN vindos do ChessVisionOFF_Puro (ver `_vendor/__init__.py`). Medido
neste PDF de 898 páginas, numa CPU sem CUDA:

    detectar os diagramas de uma página      ~55 ms
    reconhecer um tabuleiro (64 casas)       ~60 ms

O endpoint remoto continua existindo e continua escolhível — ele passa a ser reforço,
não requisito (`recognition.HybridEngine`).

`available()` responde se as dependências opcionais (`torch`, `opencv-python`,
`numpy`) estão instaladas. Sem elas nada aqui é importado e o app segue funcionando
com o motor remoto.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - exercitado por skip nos testes
    import cv2  # type: ignore  # noqa: F401
    import numpy  # type: ignore  # noqa: F401
    import torch  # type: ignore  # noqa: F401

    _DEPS_OK = True
except Exception as exc:  # pragma: no cover
    _DEPS_OK = False
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

#: Variável de ambiente que sobrepõe o modelo escolhido (scripts, CI, testes).
MODEL_ENV_VAR = "CHESS_LOCAL_MODEL"

#: Nome do arquivo distribuído com o projeto.
BUNDLED_MODEL_NAME = "piece_classifier.pt"


def dependencies_available() -> bool:
    """`torch` + `opencv-python` + `numpy` instalados?"""
    return _DEPS_OK


def bundled_model_path() -> Path:
    """`models/piece_classifier.pt` distribuído com o app.

    Fora do executável isso é a pasta `models/` do repositório; dentro dele, o
    diretório que o PyInstaller extraiu (ver `resources.asset_roots`). O caminho
    devolvido é o primeiro que existe, ou o primeiro candidato — que é o que a
    mensagem de erro deve mostrar quando não há nenhum.
    """
    from ..resources import asset_candidates, find_asset

    found = find_asset("models", BUNDLED_MODEL_NAME)
    return found or asset_candidates("models", BUNDLED_MODEL_NAME)[0]


def default_model_path(preferred: Optional[str] = None) -> Optional[Path]:
    """Primeiro modelo existente na cadeia ambiente → preferência → embutido."""
    candidates: list[Path] = []
    env_path = os.getenv(MODEL_ENV_VAR, "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if preferred:
        candidates.append(Path(preferred))
    candidates.append(bundled_model_path())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def available(preferred_model: Optional[str] = None) -> bool:
    """Dá para reconhecer localmente agora — dependências **e** modelo no lugar?"""
    return dependencies_available() and default_model_path(preferred_model) is not None


def unavailable_reason(preferred_model: Optional[str] = None) -> str:
    """Frase pronta para a UI. Vazia quando está tudo disponível."""
    if not dependencies_available():
        return (
            "Motor local indisponível: faltam as dependências opcionais. "
            "Instale com `pip install -e .[local]` "
            f"({_IMPORT_ERROR})."
        )
    if default_model_path(preferred_model) is None:
        return (
            "Motor local indisponível: modelo não encontrado. Esperado em "
            f"{bundled_model_path()}, ou aponte outro em Avançado / {MODEL_ENV_VAR}."
        )
    return ""


def get_recognizer(preferred_model: Optional[str] = None):
    """`LocalRecognizer` pronto. Levanta `LocalOcrError` se não der para usar.

    Importa `engine` só aqui: sem isso, um `import chess_pdf_editor.local_ocr` num
    ambiente sem torch quebraria o app inteiro em vez de desabilitar uma opção.
    """
    from .engine import LocalRecognizer

    return LocalRecognizer(model_path=default_model_path(preferred_model))
