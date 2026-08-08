# -*- mode: python ; coding: utf-8 -*-
"""Empacotamento do executável Windows (Sprint 8.1).

Rode pelo script, que confere o ambiente antes:

    python scripts/build_exe.py

Ou diretamente:

    pyinstaller packaging/chess_pdf_editor.spec --noconfirm

### O que vai junto

O executável precisa ser autossuficiente numa máquina sem Python: além do código,
entram o classificador (`models/piece_classifier.pt`) e os assets que o app procura
em disco. `resources.asset_roots()` sabe encontrá-los dentro do bundle — é por isso
que os caminhos aqui reproduzem a estrutura do repositório (`models/`, `assets/`).

### Por que `--onedir` e não `--onefile`

`--onefile` extrai ~2 GB (torch, OpenCV, Qt) para um diretório temporário a **cada
abertura**, o que adiciona dezenas de segundos ao arranque e recria o custo toda
vez. `--onedir` paga isso uma vez, na instalação. Para um app desktop que o usuário
abre várias vezes por dia, com um livro de 900 páginas para processar, a escolha é
clara.

### As exclusões

Cuidado aqui: `torch/__init__.py` e `torchvision/__init__.py` importam submódulos
que *parecem* dispensáveis para inferência. Medido nesta instalação, um simples
`import torch` já carrega `torch.distributed` e `torch.testing`, e um
`import torchvision` carrega `torchvision.datasets` e `torchvision.io`. Excluí-los
não enxuga o bundle: quebra o import inteiro, e o sintoma no app é um lacônico
"motor local indisponível".

A regra, então: só entra em `excludes` o que **não aparece em `sys.modules`** depois
de importar o pacote. Para conferir antes de acrescentar uma linha:

    python -c "import sys, torch; print('torch.X' in sys.modules)"

O `scripts/build_exe.py` roda o executável gerado com `--self-test` no fim, então
uma exclusão errada aparece como falha de build — foi assim que as quatro acima
foram pegas.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

APP_NAME = "ChessPdfEditor"

# (origem no disco, destino dentro do bundle). O destino espelha o repositório
# porque é assim que `resources.asset_roots()` procura.
datas = [
    (str(ROOT / "models" / "piece_classifier.pt"), "models"),
]

for optional in (
    ROOT / "assets" / "piece_images",
    ROOT / "assets" / "fonts",
):
    if optional.is_dir() and any(optional.iterdir()):
        datas.append((str(optional), str(Path("assets") / optional.name)))

# PyMuPDF e OpenCV carregam bibliotecas nativas que o analisador estático não vê.
binaries = collect_dynamic_libs("fitz") + collect_dynamic_libs("cv2")

hiddenimports = [
    # Importado dentro de uma função (`MobileNetClassifier.__init__`), então o
    # analisador estático não o alcança pela árvore de imports.
    "torchvision.models",
    # `local_ocr.engine` importa tudo tarde, de propósito, para o app abrir sem
    # as dependências opcionais instaladas.
    "chess_pdf_editor.local_ocr.engine",
    "chess_pdf_editor.local_ocr._vendor.inference",
    "chess_pdf_editor.local_ocr._vendor.board_detection",
    "chess_pdf_editor.local_ocr._vendor.decode",
]

excludes = [
    # Ferramentas de desenvolvimento e treino: nada disso roda na inferência.
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "ruff",
    # Verificado: não entra em `sys.modules` com um `import torch`.
    "torch.utils.tensorboard",
    # Backends de Qt que o app não usa (o PySide6 traz vários por padrão).
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtQuick",
    "PySide6.QtQml",
]

a = Analysis(
    [str(ROOT / "scripts" / "run_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # `console=False` esconderia mensagens de arranque; o app grava log em
    # arquivo, mas a janela de console é o que salva um "não abre" no campo.
    # Trocar para False depois que o build estiver estável.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
