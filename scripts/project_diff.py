"""Compara dois projetos salvos e lista o que mudou (§39.x / §40).

Uso tipico: reprocessar um livro com um OCR melhor e conferir o que ele corrigiu.

    python scripts/project_diff.py --before antigo.json --after novo.json
    python scripts/project_diff.py --before a.json --after b.json --json diff.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _utf8_stdout() -> None:
    """O console do Windows abre em cp1252 e o resumo tem `→` e acentos.

    Os outros scripts daqui contornam isso escrevendo so ASCII nas mensagens, mas
    este compartilha o texto com a janela do app, onde a tipografia certa importa.
    Entao quem se adapta e a saida: sem isto, `python scripts/project_diff.py`
    morre com `UnicodeEncodeError` no meio do relatorio.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream exotico
            pass

from chess_pdf_editor.project_diff import diff_files, format_diff


def _as_payload(diff) -> dict:
    """JSON com o suficiente para outra ferramenta decidir o que revisar."""
    return {
        "mesma_origem": diff.same_source,
        "origem_antes": diff.source_before,
        "origem_depois": diff.source_after,
        "resumo": {
            "adicionadas": len(diff.added),
            "removidas": len(diff.removed),
            "alteradas": len(diff.changed),
            "alteradas_com_fen_diferente": len(diff.fen_changes),
            "iguais": diff.unchanged,
            "apagamentos_adicionados": len(diff.erases_added),
            "apagamentos_removidos": len(diff.erases_removed),
        },
        "adicionadas": [asdict(op) for op in diff.added],
        "removidas": [asdict(op) for op in diff.removed],
        "alteradas": [
            {
                "pagina": item.page_num + 1,
                "motivos": list(item.reasons),
                "antes": asdict(item.before),
                "depois": asdict(item.after),
            }
            for item in diff.changed
        ],
        "ajustes": [
            {"nome": name, "antes": before, "depois": after}
            for name, before, after in diff.settings
        ],
    }


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Compara dois project_state.json e lista o que mudou."
    )
    parser.add_argument("--before", required=True, help="JSON do projeto anterior")
    parser.add_argument("--after", required=True, help="JSON do projeto novo")
    parser.add_argument("--json", help="Grava o diff completo neste arquivo JSON")
    parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Quantos itens listar por seção no resumo de texto (padrão: 40)",
    )
    args = parser.parse_args()

    for label, value in (("--before", args.before), ("--after", args.after)):
        if not Path(value).exists():
            print(f"Projeto de {label} nao encontrado: {value}")
            return 1

    diff = diff_files(args.before, args.after)
    print(format_diff(diff, limit=max(1, args.limit)))

    if args.json:
        Path(args.json).write_text(
            json.dumps(_as_payload(diff), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON gravado em {args.json}")

    # Codigo de saida util em script: 0 = igual, 1 = houve diferenca, 2 = livros
    # diferentes (o diff nao quer dizer nada).
    if not diff.same_source:
        return 2
    return 1 if diff.has_changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
