"""Build reproduzível do executável Windows (Sprint 8.1).

    python scripts/build_exe.py            # build completo + verificação
    python scripts/build_exe.py --check    # só confere o ambiente, não constrói
    python scripts/build_exe.py --skip-smoke

O ponto deste script não é chamar o PyInstaller — isso o `.spec` faz sozinho. É
**falhar cedo e falhar explicado**:

1. antes de construir, confere que as dependências opcionais e o modelo estão no
   lugar. Um build sem `torch` gera um executável que abre e diz "motor local
   indisponível", e a pessoa só descobre depois de distribuir;
2. depois de construir, **abre o executável** com `--self-test` e confere que ele
   encontra o modelo e os assets *dentro do bundle*. É a checagem que pega o erro
   clássico de empacotamento — caminho que funcionava rodando do repositório e
   deixa de funcionar congelado.

O passo 2 roda o `.exe` de verdade; sem ele o build "verde" não prova nada.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "chess_pdf_editor.spec"
DIST = ROOT / "dist" / "ChessPdfEditor"
EXE = DIST / "ChessPdfEditor.exe"
SMOKE_TIMEOUT_SEC = 180


def _fail(message: str) -> None:
    print(f"ERRO: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_environment() -> None:
    if not SPEC.is_file():
        _fail(f"spec não encontrado: {SPEC}")

    missing: list[str] = []
    for module, package in (
        ("PyInstaller", "pyinstaller"),
        ("PySide6", "PySide6"),
        ("fitz", "PyMuPDF"),
        ("torch", "torch"),
        ("torchvision", "torchvision"),
        ("cv2", "opencv-python-headless"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        _fail(
            "faltam pacotes para o build: "
            + ", ".join(missing)
            + "\n  pip install pyinstaller"
            + '\n  pip install -e ".[local]"'
        )

    sys.path.insert(0, str(ROOT / "src"))
    from chess_pdf_editor import local_ocr

    model = local_ocr.default_model_path()
    if model is None:
        _fail(
            "modelo do classificador não encontrado; o executável sairia sem motor "
            f"local. Esperado em {local_ocr.bundled_model_path()}"
        )
    print(f"modelo: {model} ({model.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"python: {sys.version.split()[0]}  |  spec: {SPEC.relative_to(ROOT)}")


def build(clean: bool) -> None:
    if clean:
        for path in (ROOT / "build", ROOT / "dist"):
            if path.exists():
                print(f"removendo {path.relative_to(ROOT)}/")
                shutil.rmtree(path)

    print("construindo (isto leva vários minutos com torch)...")
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--distpath", str(ROOT / "dist")],
        cwd=ROOT,
    )
    if result.returncode != 0:
        _fail(f"PyInstaller falhou com código {result.returncode}")
    print(f"build concluído em {(time.monotonic() - started) / 60:.1f} min")


def _folder_size_mb(path: Path) -> float:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 1024 / 1024


def smoke_test() -> None:
    """Abre o executável e confere que ele se encontra por dentro."""
    if not EXE.is_file():
        _fail(f"executável não encontrado: {EXE}")

    print(f"dist: {DIST} ({_folder_size_mb(DIST):.0f} MB)")
    print("rodando o auto-teste do executável...")
    result = subprocess.run(
        [str(EXE), "--self-test"],
        capture_output=True,
        text=True,
        timeout=SMOKE_TIMEOUT_SEC,
        # A pasta de trabalho é de propósito **outra**: rodando de dentro do
        # repositório, o app acharia `models/` e `assets/` do código-fonte e o
        # teste passaria sem provar nada sobre o bundle.
        cwd=str(Path.home()),
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        _fail("o auto-teste do executável falhou (veja acima)")
    print("auto-teste OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="só confere o ambiente")
    parser.add_argument("--skip-smoke", action="store_true", help="não roda o auto-teste")
    parser.add_argument("--no-clean", action="store_true", help="reaproveita build/ e dist/")
    args = parser.parse_args()

    check_environment()
    if args.check:
        return
    build(clean=not args.no_clean)
    if not args.skip_smoke:
        smoke_test()
    print(f"\npronto: {EXE}")


if __name__ == "__main__":
    main()
