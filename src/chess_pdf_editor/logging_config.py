"""Logging estruturado do aplicativo.

Motivacao (§22.4 do plano): havia `except Exception: pass` em pontos criticos
— render do diagrama, carregamento de projeto, fingerprint do PDF. Quando algo
falhava o usuario via um resultado errado sem nenhuma pista da causa.

A regra adotada: **mensagem amigavel na UI, detalhe tecnico no arquivo**. Os
handlers continuam engolindo a excecao (o app nao deve morrer porque um
diagrama nao renderizou), mas agora deixam rastro.

O arquivo fica em `%LOCALAPPDATA%/ChessPdfEditor/logs` no Windows e em
`$XDG_STATE_HOME/ChessPdfEditor/logs` (ou `~/.local/state/...`) no Linux.
`CHESS_PDF_EDITOR_LOG_DIR` sobrescreve, e `CHESS_PDF_EDITOR_LOG_LEVEL` ajusta o
nivel. Configurar o log nunca pode derrubar o app: se o diretorio nao puder ser
criado, sobra o handler de stderr.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOGGER_NAME = "chess_pdf_editor"
LOG_DIR_ENV_VAR = "CHESS_PDF_EDITOR_LOG_DIR"
LOG_LEVEL_ENV_VAR = "CHESS_PDF_EDITOR_LOG_LEVEL"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False
_log_file_path: Optional[Path] = None


def default_log_dir() -> Path:
    """Diretorio de logs conforme a plataforma (respeitando o override)."""
    override = os.getenv(LOG_DIR_ENV_VAR, "").strip()
    if override:
        return Path(override)

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        base = Path(local_app_data)
    else:
        xdg_state = os.getenv("XDG_STATE_HOME", "").strip()
        base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "ChessPdfEditor" / "logs"


def _resolve_level(level: Optional[int]) -> int:
    if level is not None:
        return level
    raw = os.getenv(LOG_LEVEL_ENV_VAR, "").strip().upper()
    if not raw:
        return logging.INFO
    named = getattr(logging, raw, None)
    if isinstance(named, int):
        return named
    try:
        return int(raw)
    except ValueError:
        return logging.INFO


def setup_logging(
    log_dir: Optional[Path] = None,
    level: Optional[int] = None,
    to_stderr: bool = True,
    force: bool = False,
) -> logging.Logger:
    """Configura o logger raiz do app. Idempotente e a prova de falhas."""
    global _configured, _log_file_path

    logger = logging.getLogger(LOGGER_NAME)
    if _configured and not force:
        return logger

    resolved_level = _resolve_level(level)
    logger.setLevel(resolved_level)
    # `propagate=False` evita que a configuracao de logging do processo
    # hospedeiro (pytest, por exemplo) duplique cada linha.
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(_LOG_FORMAT)

    target_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    _log_file_path = None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "chess_pdf_editor.log"
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        _log_file_path = file_path
    except Exception:
        # Sem permissao de escrita (rede, pasta protegida): segue so com stderr.
        pass

    if to_stderr:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    _configured = True
    return logger


def log_file_path() -> Optional[Path]:
    """Caminho do arquivo de log ativo, ou None se so ha stderr."""
    return _log_file_path


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Logger do modulo. Nao configura nada — quem chama pode ser importado
    por um script sem GUI que ja tenha seu proprio logging."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
