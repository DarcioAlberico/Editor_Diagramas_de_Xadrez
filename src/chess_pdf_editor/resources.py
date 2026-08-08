"""Onde procurar os arquivos que acompanham o app (Sprint 8.1).

Rodando do repositório, os assets ficam ao lado do código: `models/` e `assets/`
estão na raiz, e `Path(__file__).parents[N]` acha os dois. Dentro de um executável
PyInstaller isso deixa de valer — o código vive num arquivo compactado e os dados
são extraídos para um diretório temporário cujo caminho está em `sys._MEIPASS`.

Antes deste módulo cada busca resolvia o caminho por conta própria, com contagens
de `parents[]` diferentes (2 no `widgets`, 3 no `local_ocr`, 4 no `_vendor`). Uma
delas quebraria no empacotamento, e a que quebrasse seria descoberta só quando
alguém abrisse o `.exe` numa máquina limpa e o motor local dissesse "modelo não
encontrado" sem explicar por quê.

`asset_roots()` devolve os lugares em ordem de prioridade, e quem procura só
percorre a lista. Manter a pasta de trabalho na lista é o que permite ao usuário
do executável largar uma fonte Merida ao lado do `.exe` e ela ser encontrada.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

#: Diretório de extração do PyInstaller, quando existe.
_MEIPASS_ATTR = "_MEIPASS"


def is_frozen() -> bool:
    """Rodando de dentro de um executável empacotado?"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path | None:
    """Diretório onde o PyInstaller extraiu os dados. `None` fora do executável."""
    meipass = getattr(sys, _MEIPASS_ATTR, None)
    return Path(meipass) if meipass else None


def repo_root() -> Path:
    """Raiz do projeto quando se roda do código-fonte.

    `parents[2]` sobe de `src/chess_pdf_editor/resources.py` até a raiz. É a única
    contagem de `parents` que sobrou no projeto, e ela mora aqui.
    """
    return Path(__file__).resolve().parents[2]


def executable_dir() -> Path:
    """Pasta do `.exe` (ou do interpretador). É onde o usuário larga arquivos."""
    return Path(sys.executable).resolve().parent


def asset_roots() -> Iterator[Path]:
    """Lugares onde procurar assets, do mais específico para o mais genérico."""
    seen: set[Path] = set()
    candidates = [bundle_root(), repo_root(), Path.cwd()]
    if is_frozen():
        # Ao lado do executável: como o usuário acrescenta uma fonte própria sem
        # reempacotar nada.
        candidates.insert(1, executable_dir())
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = Path(candidate).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield resolved


def find_asset(*relative_parts: str) -> Path | None:
    """Primeiro caminho existente para `relative_parts` entre as raízes conhecidas."""
    for root in asset_roots():
        candidate = root.joinpath(*relative_parts)
        if candidate.exists():
            return candidate
    return None


def asset_candidates(*relative_parts: str) -> list[Path]:
    """Todos os caminhos possíveis, existindo ou não — para montar listas de busca."""
    return [root.joinpath(*relative_parts) for root in asset_roots()]
