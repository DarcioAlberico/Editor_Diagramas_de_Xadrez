"""Variante `light` do executável: mesmo app, sem o motor local (§44).

Medido nos dois pacotes gerados lado a lado: **719 MB** completo, **193 MB** light.

O que se testa aqui é o que muda de *comportamento* quando o bundle se declara light —
não o empacotamento em si, que só um build de verdade prova (e o `build_exe.py --light`
faz isso, rodando o `--self-test` do executável gerado no fim).

A variante é lida de um marcador que o `.spec` grava **dentro** do bundle. Fora dele o
app é sempre `full`, porque rodando do código-fonte "sem motor local" quer dizer
"instale as dependências opcionais" — conselho que ali funciona.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chess_pdf_editor import resources
from chess_pdf_editor.recognition import ENGINE_HYBRID, ENGINE_REMOTE, default_engine_mode


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch):
    """Finge um bundle do PyInstaller, com marcador opcional."""

    def _make(variant: str | None) -> Path:
        root = tmp_path / "bundle"
        root.mkdir(exist_ok=True)
        if variant is not None:
            (root / resources.VARIANT_FILE).write_text(variant, encoding="utf-8")
        monkeypatch.setattr(resources, "bundle_root", lambda: root)
        return root

    return _make


# ---------------------------------------------------------------------------
# Leitura do marcador
# ---------------------------------------------------------------------------


def test_running_from_source_is_always_the_full_variant() -> None:
    """Sem bundle não há marcador, e o padrão não pode ser "light" por omissão."""
    assert resources.bundle_root() is None
    assert resources.build_variant() == resources.VARIANT_FULL
    assert resources.is_light_build() is False


def test_a_bundle_marked_light_is_light(bundle) -> None:
    bundle("light")
    assert resources.build_variant() == resources.VARIANT_LIGHT
    assert resources.is_light_build() is True


def test_a_bundle_marked_full_is_full(bundle) -> None:
    bundle("full")
    assert resources.is_light_build() is False


def test_a_bundle_without_a_marker_is_full(bundle) -> None:
    """Bundle antigo, gerado antes da variante existir: tem de continuar completo."""
    bundle(None)
    assert resources.is_light_build() is False


@pytest.mark.parametrize("written", ["LIGHT", " light \n", "Light"])
def test_the_marker_is_read_forgivingly(bundle, written: str) -> None:
    bundle(written)
    assert resources.is_light_build() is True


def test_an_unreadable_marker_falls_back_to_full(bundle, monkeypatch) -> None:
    """Errar para o lado de "completo" é o seguro: no pior caso o app diz que falta
    instalar dependência, em vez de afirmar que a distribuição não as tem."""
    root = bundle("light")
    marker = root / resources.VARIANT_FILE
    marker.unlink()
    marker.mkdir()  # um diretório com o nome do arquivo: leitura falha

    assert resources.build_variant() == resources.VARIANT_FULL


# ---------------------------------------------------------------------------
# O que a variante muda
# ---------------------------------------------------------------------------


def test_the_light_build_defaults_to_the_remote_engine(bundle) -> None:
    """O padrão híbrido precisa do motor local; num pacote sem ele seria uma promessa
    que o executável não pode cumprir. Pego pela saída do `--self-test` do build."""
    assert default_engine_mode() == ENGINE_HYBRID

    bundle("light")
    assert default_engine_mode() == ENGINE_REMOTE


def test_the_light_build_does_not_tell_the_user_to_run_pip(bundle, monkeypatch) -> None:
    """Quem recebeu o `.exe` não tem Python: `pip install` é conselho impossível."""
    from chess_pdf_editor import local_ocr

    monkeypatch.setattr(local_ocr, "_DEPS_OK", False)

    bundle("light")
    light_reason = local_ocr.unavailable_reason()
    assert "pip install" not in light_reason
    assert "download menor" in light_reason

    bundle("full")
    full_reason = local_ocr.unavailable_reason()
    assert "pip install" in full_reason, "no código-fonte o conselho continua sendo esse"


def test_a_saved_choice_still_wins_over_the_variant_default(main_window, bundle) -> None:
    """A variante decide só o padrão; o que o usuário escolheu continua valendo."""
    from chess_pdf_editor import app as app_module

    main_window.settings.setValue("recognition_engine", ENGINE_HYBRID)
    bundle("light")

    reopened = app_module.MainWindow(settings=main_window.settings)
    try:
        assert reopened._engine_mode() == ENGINE_HYBRID
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# O script de build
# ---------------------------------------------------------------------------


def test_the_two_variants_do_not_share_a_folder() -> None:
    """Uma sobrescreveria a outra, e a comparação de tamanho ficaria impossível."""
    import importlib.util

    spec_path = Path(__file__).resolve().parents[1] / "scripts" / "build_exe.py"
    spec = importlib.util.spec_from_file_location("build_exe_under_test", spec_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.dist_dir(light=True) != module.dist_dir(light=False)
    # O executável tem o mesmo nome nas duas: é o mesmo app.
    assert module.exe_path(light=True).name == module.exe_path(light=False).name


def test_the_spec_reacts_to_the_light_flag() -> None:
    """O `.spec` decide pela variável de ambiente que o script exporta; se os dois
    nomes divergirem, `--light` construiria silenciosamente o pacote completo."""
    root = Path(__file__).resolve().parents[1]
    spec_text = (root / "packaging" / "chess_pdf_editor.spec").read_text(encoding="utf-8")
    script_text = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")

    assert 'os.environ.get("CHESS_PDF_EDITOR_LIGHT", "") == "1"' in spec_text
    assert 'LIGHT_ENV_VAR = "CHESS_PDF_EDITOR_LIGHT"' in script_text
    # O que a variante existe para deixar de fora.
    for excluded in ('"torch"', '"torchvision"', '"cv2"'):
        assert excluded in spec_text
