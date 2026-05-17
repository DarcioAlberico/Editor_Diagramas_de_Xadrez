from __future__ import annotations

import importlib
import sys

MODULES = [
    "PySide6",
    "fitz",
    "PIL",
    "requests",
    "chess",
]


def main() -> int:
    print(f"Python: {sys.version}")
    ok = True
    for module_name in MODULES:
        try:
            mod = importlib.import_module(module_name)
            version = getattr(mod, "__version__", "unknown")
            print(f"[OK] {module_name} ({version})")
        except Exception as exc:
            ok = False
            print(f"[FAIL] {module_name}: {exc}")

    try:
        import cairosvg  # type: ignore

        print(f"[OK] cairosvg ({getattr(cairosvg, '__version__', 'unknown')})")
    except Exception as exc:
        print(f"[WARN] cairosvg nao disponivel (fallback raster ativo): {exc}")

    if ok:
        print("Ambiente valido para executar o app.")
        return 0
    print("Ambiente incompleto. Instale as dependencias e rode novamente.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

