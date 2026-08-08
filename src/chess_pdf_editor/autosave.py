"""Autosave do projeto (Sprint 5.3).

O objetivo do sprint e "nunca perder trabalho". Reconhecer um livro de 900
paginas e conferir os candidatos leva horas; um travamento antes do primeiro
`Salvar projeto` jogava tudo fora.

**Onde grava.** Se o usuario ja escolheu um arquivo de projeto, o autosave
escreve nele — foi o arquivo que ele pediu. Enquanto nao escolheu, grava num
diretorio proprio do app, com nome derivado do caminho do PDF. Isso evita
espalhar `.json` pelas pastas de livros do usuario e ainda assim da um caminho
estavel: reabrir o mesmo PDF reencontra o mesmo autosave.

**Como grava.** Sempre em arquivo temporario + `os.replace`. Um autosave
interrompido no meio (queda de energia, kill) nao pode deixar para tras um JSON
truncado no lugar do projeto bom — `os.replace` e atomico no Windows e no POSIX.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from .logging_config import get_logger
from .project_state import ProjectState, save_project_state

logger = get_logger("autosave")

AUTOSAVE_DIR_ENV_VAR = "CHESS_PDF_EDITOR_AUTOSAVE_DIR"
DEFAULT_INTERVAL_SEC = 120
MIN_INTERVAL_SEC = 15
AUTOSAVE_SUFFIX = ".autosave.json"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def default_autosave_dir() -> Path:
    override = os.getenv(AUTOSAVE_DIR_ENV_VAR, "").strip()
    if override:
        return Path(override)

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        base = Path(local_app_data)
    else:
        xdg_state = os.getenv("XDG_STATE_HOME", "").strip()
        base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "ChessPdfEditor" / "autosave"


def _safe_stem(stem: str) -> str:
    cleaned = _UNSAFE_CHARS.sub("_", stem).strip("_")
    return (cleaned or "projeto")[:60]


def autosave_path_for_pdf(pdf_path: str, base_dir: Optional[Path] = None) -> Path:
    """Caminho estavel de autosave para um PDF.

    O hash do caminho absoluto desempata dois livros de mesmo nome em pastas
    diferentes; o nome legivel na frente serve para o usuario reconhecer o
    arquivo se precisar mexer nele a mao.
    """
    resolved = Path(pdf_path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    digest = hashlib.sha256(str(resolved).encode("utf-8", "surrogateescape")).hexdigest()[:16]
    directory = Path(base_dir) if base_dir is not None else default_autosave_dir()
    return directory / f"{_safe_stem(resolved.stem)}-{digest}{AUTOSAVE_SUFFIX}"


def is_autosave_path(path: str) -> bool:
    return str(path).endswith(AUTOSAVE_SUFFIX)


def write_project_atomically(path: str, state: ProjectState) -> None:
    """Grava o projeto sem risco de deixar um arquivo pela metade."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".tmp")
    save_project_state(str(tmp_path), state)
    os.replace(tmp_path, target)
