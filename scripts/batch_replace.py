from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chess_pdf_editor.pdf_service import apply_operations_to_pdf
from chess_pdf_editor.project_state import load_project_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica substituicoes de um projeto em batch.")
    parser.add_argument("--project", required=True, help="JSON do projeto salvo")
    parser.add_argument("--output", required=True, help="PDF de saida")
    parser.add_argument("--no-whiteout", action="store_true", help="Desabilita whiteout")
    parser.add_argument("--no-lichess-link", action="store_true", help="Nao insere link Lichess no PDF")
    args = parser.parse_args()

    project_path = Path(args.project)
    if not project_path.exists():
        print(f"Projeto nao encontrado: {project_path}")
        return 1

    state = load_project_state(str(project_path))
    if not Path(state.source_pdf).exists():
        print(f"PDF de origem nao encontrado: {state.source_pdf}")
        return 1

    apply_operations_to_pdf(
        state.source_pdf,
        args.output,
        state.operations,
        erase_operations=state.erase_operations,
        whiteout=not args.no_whiteout,
        include_lichess_link=(not args.no_lichess_link) and bool(getattr(state, "include_lichess_link", True)),
    )
    print(f"PDF salvo: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
