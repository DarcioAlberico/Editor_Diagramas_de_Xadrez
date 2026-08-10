"""Build reproduzível do executável Windows (Sprint 8.1).

    python scripts/build_exe.py                  # build completo + verificação
    python scripts/build_exe.py --check          # só confere o ambiente, não constrói
    python scripts/build_exe.py --skip-smoke
    python scripts/build_exe.py --light --installer   # variante leve + instalador

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

Com `--installer`, um passo 3 compila `packaging/installer.iss` com o Inno Setup —
depois do auto-teste, para não gastar minutos de compressão empacotando um bundle
reprovado. Esse passo exige o Inno Setup instalado e diz como obtê-lo quando não
está (§49).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "chess_pdf_editor.spec"
INSTALLER_ISS = ROOT / "packaging" / "installer.iss"
SMOKE_TIMEOUT_SEC = 180
INSTALLER_TIMEOUT_SEC = 1800

#: Variante light (§44): mesmo app, sem o motor local, para um download menor.
LIGHT_ENV_VAR = "CHESS_PDF_EDITOR_LIGHT"

#: Onde procurar o compilador do Inno Setup, em ordem. A variável de ambiente vem
#: primeiro para quem o instalou fora do lugar padrão (§49.2).
ISCC_ENV_VAR = "INNO_SETUP_ISCC"
ISCC_DEFAULT_PATHS = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def dist_dir(light: bool) -> Path:
    """Cada variante na sua pasta, senão uma sobrescreveria a outra."""
    return ROOT / "dist" / ("ChessPdfEditor-lite" if light else "ChessPdfEditor")


def exe_path(light: bool) -> Path:
    # O nome do executável é o mesmo nas duas: é o mesmo app.
    return dist_dir(light) / "ChessPdfEditor.exe"


def _fail(message: str) -> None:
    print(f"ERRO: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_environment(light: bool = False) -> None:
    if not SPEC.is_file():
        _fail(f"spec não encontrado: {SPEC}")

    required = [
        ("PyInstaller", "pyinstaller"),
        ("PySide6", "PySide6"),
        ("fitz", "PyMuPDF"),
    ]
    if not light:
        # A variante light não empacota nada disso, então exigi-la instalada só
        # tornaria o build menor mais difícil de produzir.
        required += [
            ("torch", "torch"),
            ("torchvision", "torchvision"),
            ("cv2", "opencv-python-headless"),
            ("numpy", "numpy"),
        ]

    missing: list[str] = []
    for module, package in required:
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

    if light:
        print("variante: light (sem motor local)")
    else:
        model = local_ocr.default_model_path()
        if model is None:
            _fail(
                "modelo do classificador não encontrado; o executável sairia sem motor "
                f"local. Esperado em {local_ocr.bundled_model_path()}"
            )
        print(f"modelo: {model} ({model.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"python: {sys.version.split()[0]}  |  spec: {SPEC.relative_to(ROOT)}")


def build(clean: bool, light: bool = False) -> None:
    if clean:
        # Só a pasta da variante que está sendo construída: apagar `dist/` inteiro
        # jogaria fora a outra variante, que é justamente com o que se compara.
        for path in (ROOT / "build", dist_dir(light)):
            if path.exists():
                print(f"removendo {path.relative_to(ROOT)}/")
                shutil.rmtree(path)

    print("construindo a variante light..." if light else "construindo (vários minutos com torch)...")
    started = time.monotonic()
    environment = dict(os.environ)
    environment[LIGHT_ENV_VAR] = "1" if light else "0"
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--distpath", str(ROOT / "dist")],
        cwd=ROOT,
        env=environment,
    )
    if result.returncode != 0:
        _fail(f"PyInstaller falhou com código {result.returncode}")
    print(f"build concluído em {(time.monotonic() - started) / 60:.1f} min")


def _folder_size_mb(path: Path) -> float:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 1024 / 1024


def smoke_test(light: bool = False) -> None:
    """Abre o executável e confere que ele se encontra por dentro.

    Vale para as duas variantes: o `--self-test` do app lê o marcador do bundle e
    checa o contrato **daquela** variante — na light, que o motor local ficou de fora
    de verdade (§44.4).
    """
    exe = exe_path(light)
    dist = dist_dir(light)
    if not exe.is_file():
        _fail(f"executável não encontrado: {exe}")

    print(f"dist: {dist} ({_folder_size_mb(dist):.0f} MB)")
    print("rodando o auto-teste do executável...")
    result = subprocess.run(
        [str(exe), "--self-test"],
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


def project_version() -> str:
    """A versão do `pyproject.toml` — a única fonte que existe.

    O `.iss` traz um padrão para quem chama o ISCC à mão, mas quem compila pelo
    script recebe este valor por `/D`. O teste da §49.3 confere que os dois não
    divergiram.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if match is None:
        _fail("não achei `version` no pyproject.toml")
    return match.group(1)


def find_iscc() -> Path | None:
    """O compilador do Inno Setup, ou `None` se esta máquina não o tem."""
    override = os.environ.get(ISCC_ENV_VAR)
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    for candidate in ISCC_DEFAULT_PATHS:
        if candidate.is_file():
            return candidate
    return None


def installer_path(light: bool, version: str) -> Path:
    """Espelha o `OutputBaseFilename` do `.iss`; um teste confere o espelho."""
    suffix = "lite" if light else "full"
    return ROOT / "dist" / f"ChessPdfEditor-{version}-{suffix}-setup.exe"


def build_installer(light: bool = False) -> None:
    """Compila o `.iss` da variante recém-construída.

    Falha alto quando o Inno Setup não está instalado, em vez de seguir em
    silêncio: quem passou `--installer` pediu um instalador, e terminar verde sem
    produzir um é a forma de o build mentir (mesma escolha da §33 e da §47).
    """
    if not INSTALLER_ISS.is_file():
        _fail(f"script do instalador não encontrado: {INSTALLER_ISS}")

    dist = dist_dir(light)
    if not dist.is_dir():
        _fail(f"não há o que empacotar: {dist} não existe (construa antes)")

    iscc = find_iscc()
    if iscc is None:
        _fail(
            "Inno Setup não encontrado nesta máquina, e o instalador foi pedido.\n"
            "  Instale o Inno Setup 6 (https://jrsoftware.org/isdl.php) ou aponte\n"
            f"  {ISCC_ENV_VAR} para o ISCC.exe.\n"
            "  O resto do build não depende disto: sem `--installer` a pasta de\n"
            f"  distribuição em {dist.relative_to(ROOT)}/ continua sendo a entrega."
        )

    version = project_version()
    print(f"compilando o instalador com {iscc}...")
    started = time.monotonic()
    result = subprocess.run(
        [
            str(iscc),
            f"/DAppVersion={version}",
            f"/DVariant={'light' if light else 'full'}",
            f"/DSourceDir={dist}",
            f"/DOutputDir={ROOT / 'dist'}",
            str(INSTALLER_ISS),
        ],
        cwd=ROOT,
        timeout=INSTALLER_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        _fail(f"o Inno Setup falhou com código {result.returncode}")

    produced = installer_path(light, version)
    if not produced.is_file():
        # O ISCC pode terminar 0 e gravar noutro nome se alguém mexer no
        # `OutputBaseFilename` sem mexer aqui. Melhor acusar que anunciar um
        # arquivo que não existe.
        _fail(f"o Inno Setup terminou bem, mas {produced.name} não apareceu em dist/")
    size_mb = produced.stat().st_size / 1024 / 1024
    print(
        f"instalador: {produced} ({size_mb:.0f} MB) "
        f"em {(time.monotonic() - started) / 60:.1f} min"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="só confere o ambiente")
    parser.add_argument("--skip-smoke", action="store_true", help="não roda o auto-teste")
    parser.add_argument("--no-clean", action="store_true", help="reaproveita build/ e dist/")
    parser.add_argument(
        "--light",
        action="store_true",
        help="build sem o motor local: download menor, reconhecimento pelo serviço externo",
    )
    parser.add_argument(
        "--installer",
        action="store_true",
        help="também compila o instalador (.exe) com o Inno Setup",
    )
    args = parser.parse_args()

    check_environment(light=args.light)
    if args.check:
        return
    build(clean=not args.no_clean, light=args.light)
    if not args.skip_smoke:
        smoke_test(light=args.light)
    print(f"\npronto: {exe_path(args.light)}")
    if args.installer:
        # Depois do auto-teste, de propósito: empacotar um bundle que não passou
        # no próprio teste é gastar minutos de compressão para distribuir um erro.
        build_installer(light=args.light)


if __name__ == "__main__":
    main()
