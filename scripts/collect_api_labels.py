from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chess_pdf_editor.ocr_api import OcrApiClient


def find_images(input_dir: Path) -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
    out: list[Path] = []
    for path in input_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            out.append(path)
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta pseudo-labels FEN via API OCR.")
    parser.add_argument("--input", required=True, help="Pasta com imagens")
    parser.add_argument("--output", required=True, help="Arquivo JSONL de saida")
    parser.add_argument(
        "--endpoint",
        default="https://helpman.komtera.lt/chessocr/predict",
        help="Endpoint OCR",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"Pasta nao encontrada: {input_dir}")
        return 1

    images = find_images(input_dir)
    if not images:
        print("Nenhuma imagem encontrada.")
        return 1

    client = OcrApiClient(endpoint=args.endpoint)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(images)
    ok_count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for idx, img_path in enumerate(images, start=1):
            print(f"[{idx}/{total}] {img_path}")
            try:
                data = img_path.read_bytes()
                pred = client.predict(data, filename=img_path.name)
                record = {
                    "image": str(img_path),
                    "request_id": pred.request_id,
                    "status": pred.status,
                    "message": pred.message,
                    "results": [
                        {
                            "fen": r.fen,
                            "xc": r.xc,
                            "yc": r.yc,
                            "width": r.width,
                            "height": r.height,
                        }
                        for r in pred.results
                    ],
                }
                if pred.results:
                    ok_count += 1
            except Exception as exc:
                record = {
                    "image": str(img_path),
                    "error": str(exc),
                    "results": [],
                }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Concluido. Registros com resultados: {ok_count}/{total}")
    print(f"Arquivo: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

