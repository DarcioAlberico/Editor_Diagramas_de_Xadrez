from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz  # type: ignore
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chess_pdf_editor.project_state import load_project_state


def _collect_project_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix.lower() == ".json":
            out.append(p)
            continue
        if p.is_dir():
            out.extend(sorted(p.rglob("*.json")))
            continue
        print(f"[WARN] Caminho ignorado (nao encontrado): {p}")
    return out


def _resolve_source_pdf(project_path: Path, source_pdf: str) -> Path:
    p = Path(source_pdf)
    if p.exists():
        return p
    if not p.is_absolute():
        candidate = (project_path.parent / p).resolve()
        if candidate.exists():
            return candidate
    return p


def _expand_and_clip_rect(rect: fitz.Rect, page_rect: fitz.Rect, expand_pt: float) -> fitz.Rect:
    out = fitz.Rect(rect)
    if expand_pt > 0:
        out = fitz.Rect(
            out.x0 - expand_pt,
            out.y0 - expand_pt,
            out.x1 + expand_pt,
            out.y1 + expand_pt,
        )
    out = out & page_rect
    return out


def _to_square_rect(rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    cx = (rect.x0 + rect.x1) * 0.5
    cy = (rect.y0 + rect.y1) * 0.5
    side = max(rect.width, rect.height)
    candidate = fitz.Rect(cx - side * 0.5, cy - side * 0.5, cx + side * 0.5, cy + side * 0.5)
    candidate = candidate & page_rect
    if candidate.is_empty:
        return rect
    # Reajusta para quadrado apos clipping, preservando centro aproximado.
    side2 = min(candidate.width, candidate.height)
    cx2 = (candidate.x0 + candidate.x1) * 0.5
    cy2 = (candidate.y0 + candidate.y1) * 0.5
    return fitz.Rect(cx2 - side2 * 0.5, cy2 - side2 * 0.5, cx2 + side2 * 0.5, cy2 + side2 * 0.5)


def _pixmap_to_png_bytes(pix: fitz.Pixmap, target_size: int, square: bool) -> bytes:
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if target_size > 0:
        if square:
            image = image.resize((target_size, target_size), Image.Resampling.LANCZOS)
        else:
            w, h = image.size
            longest = max(w, h)
            if longest > 0 and longest != target_size:
                scale = float(target_size) / float(longest)
                image = image.resize(
                    (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                    Image.Resampling.LANCZOS,
                )
    from io import BytesIO

    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrai dataset (imagem + FEN) a partir de projetos salvos.")
    parser.add_argument(
        "--projects",
        nargs="+",
        required=True,
        help="Um ou mais JSONs de projeto, ou pastas contendo JSONs.",
    )
    parser.add_argument("--images-dir", required=True, help="Pasta para salvar as imagens recortadas")
    parser.add_argument("--labels", required=True, help="Arquivo JSONL de labels")
    parser.add_argument("--dpi", type=int, default=300, help="DPI para render do recorte (default: 300)")
    parser.add_argument(
        "--size",
        type=int,
        default=512,
        help="Tamanho alvo em px (quadrado se --square, maior lado caso contrario)",
    )
    parser.add_argument(
        "--expand-pt",
        type=float,
        default=0.0,
        help="Expansao do retangulo em pontos PDF antes do recorte",
    )
    parser.add_argument(
        "--square",
        action="store_true",
        help="Normaliza recortes para retangulo quadrado ao redor do diagrama",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Nao sobrescreve imagens que ja existem no destino",
    )
    args = parser.parse_args()

    image_dir = Path(args.images_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    labels_path = Path(args.labels)
    labels_path.parent.mkdir(parents=True, exist_ok=True)

    project_files = _collect_project_files(args.projects)
    if not project_files:
        print("Nenhum projeto JSON encontrado.")
        return 1

    records: list[dict[str, object]] = []
    total_ops = 0
    total_saved = 0
    total_errors = 0
    dpi = max(72, int(args.dpi))
    matrix = fitz.Matrix(float(dpi) / 72.0, float(dpi) / 72.0)

    for project_path in project_files:
        print(f"[PROJECT] {project_path}")
        try:
            state = load_project_state(str(project_path))
        except Exception as exc:
            total_errors += 1
            print(f"  [ERRO] Falha ao carregar projeto: {exc}")
            continue

        source_pdf = _resolve_source_pdf(project_path, state.source_pdf)
        if not source_pdf.exists():
            total_errors += 1
            print(f"  [ERRO] PDF de origem nao encontrado: {source_pdf}")
            continue

        try:
            doc = fitz.open(str(source_pdf))
        except Exception as exc:
            total_errors += 1
            print(f"  [ERRO] Falha ao abrir PDF: {exc}")
            continue

        try:
            for op_idx, op in enumerate(state.operations, start=1):
                total_ops += 1
                if not (0 <= op.page_num < len(doc)):
                    total_errors += 1
                    print(f"  [WARN] Pagina invalida na op {op_idx}: {op.page_num}")
                    continue

                page = doc[op.page_num]
                rect = _expand_and_clip_rect(fitz.Rect(op.rect_pdf), page.rect, float(args.expand_pt))
                if args.square:
                    rect = _to_square_rect(rect, page.rect)
                if rect.is_empty or rect.width < 1 or rect.height < 1:
                    total_errors += 1
                    print(f"  [WARN] Retangulo invalido na op {op_idx}: {tuple(op.rect_pdf)}")
                    continue

                image_name = f"{project_path.stem}_p{op.page_num + 1:03d}_op{op_idx:04d}.png"
                image_path = image_dir / image_name
                if args.skip_existing and image_path.exists():
                    png_bytes = image_path.read_bytes()
                else:
                    pix = page.get_pixmap(
                        matrix=matrix,
                        clip=rect,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    png_bytes = _pixmap_to_png_bytes(pix, int(args.size), bool(args.square))
                    image_path.write_bytes(png_bytes)
                    total_saved += 1

                records.append(
                    {
                        "image": str(image_path.resolve()),
                        "fen": op.fen,
                        "project": str(project_path.resolve()),
                        "source_pdf": str(source_pdf.resolve()),
                        "page_num": op.page_num,
                        "rect_pdf": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "source": op.source,
                        "confidence": op.confidence,
                        "whiteout_padding_pt": op.whiteout_padding_pt,
                        "border_width_pt": op.border_width_pt,
                    }
                )
        finally:
            doc.close()

    with labels_path.open("w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Projetos lidos: {len(project_files)}")
    print(f"Operacoes processadas: {total_ops}")
    print(f"Imagens gravadas: {total_saved}")
    print(f"Registros no JSONL: {len(records)}")
    if total_errors:
        print(f"Avisos/erros: {total_errors}")
    print(f"Labels: {labels_path}")
    print(f"Imagens: {image_dir}")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
