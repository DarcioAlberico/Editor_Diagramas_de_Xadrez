"""Instalador Windows: o `.iss` e o passo que o compila (§49).

Nada aqui roda o Inno Setup — ele não está instalado nesta máquina, e é justamente
por isso que estes testes existem. O que dá para provar sem o compilador é que o
script e o build **concordam**: mesma versão, mesmos nomes de pasta, mesmo nome de
arquivo produzido. Cada um desses pares é mantido à mão nos dois lados, e a §45 já
mostrou o que acontece com pares assim quando ninguém os amarra.

O que só um `ISCC.exe` responde — o instalador realmente instala? — continua
pendente na §28.4, junto com a máquina limpa.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "installer.iss"


@pytest.fixture(scope="module")
def iss_text() -> str:
    return ISS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def build_exe():
    """O `build_exe.py` carregado como módulo — ele não é um pacote importável."""
    spec = importlib.util.spec_from_file_location(
        "build_exe_under_test", ROOT / "scripts" / "build_exe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _define(text: str, name: str) -> str:
    """O valor de um `#define <name> "<valor>"` do `.iss`."""
    match = re.search(rf'(?m)^\s*#define\s+{name}\s+"([^"]*)"', text)
    assert match is not None, f"o .iss não define {name}"
    return match.group(1)


# ---------------------------------------------------------------------------
# Os pares mantidos à mão nos dois lados
# ---------------------------------------------------------------------------


def test_the_iss_default_version_matches_pyproject(iss_text: str, build_exe) -> None:
    """O padrão do `.iss` é para quem chama o ISCC à mão; se ficar para trás do
    `pyproject.toml`, essa pessoa gera um instalador com a versão errada — e o
    número aparece no "Adicionar ou remover programas" de quem instalar."""
    assert _define(iss_text, "AppVersion") == build_exe.project_version()


def test_the_iss_and_the_spec_agree_on_the_app_name(iss_text: str) -> None:
    """O `.iss` procura `ChessPdfEditor.exe` dentro da pasta que o `.spec` gerou."""
    spec_text = (ROOT / "packaging" / "chess_pdf_editor.spec").read_text(encoding="utf-8")

    assert 'APP_NAME = "ChessPdfEditor"' in spec_text
    assert _define(iss_text, "AppName") == "ChessPdfEditor"
    assert _define(iss_text, "AppExeName") == "ChessPdfEditor.exe"


@pytest.mark.parametrize("light", (False, True))
def test_the_iss_packages_the_folder_the_build_produced(
    iss_text: str, build_exe, light: bool
) -> None:
    """Os nomes de pasta das duas variantes estão escritos no `.iss` e no
    `build_exe.py`. Divergindo, o instalador empacotaria a variante errada — ou
    nenhuma, se a pasta não existir."""
    produced = build_exe.dist_dir(light=light).name

    assert f'#define DistName "{produced}"' in iss_text


def test_the_two_variants_do_not_produce_the_same_installer(
    iss_text: str, build_exe
) -> None:
    """Eco do `test_the_two_variants_do_not_share_a_folder`: se os dois instaladores
    saíssem com o mesmo nome, o segundo build sobrescreveria o primeiro em `dist/` e
    quem baixasse levaria a variante errada sem nenhum aviso."""
    version = build_exe.project_version()
    full = build_exe.installer_path(light=False, version=version)
    lite = build_exe.installer_path(light=True, version=version)

    assert full != lite
    # E o nome que o script anuncia é o que o `.iss` manda gravar.
    assert "OutputBaseFilename={#AppName}-{#AppVersion}-{#SetupSuffix}-setup" in iss_text
    for path, suffix in ((full, "full"), (lite, "lite")):
        assert path.name == f"ChessPdfEditor-{version}-{suffix}-setup.exe"


# ---------------------------------------------------------------------------
# A armadilha do light por cima do full
# ---------------------------------------------------------------------------


def test_the_installer_clears_the_previous_payload(iss_text: str) -> None:
    """Instalar a variante leve por cima da completa deixaria o torch da anterior em
    `_internal`: o Inno não remove o que não está na lista nova. O bundle se diria
    `light` com o motor local ainda ao lado, quebrando pela instalação o contrato
    que a §44.4 criou o auto-teste para garantir.

    O `[InstallDelete]` mira só o `_internal` do bundle — apagar `{app}` inteiro
    destruiria o que não é nosso se alguém instalar numa pasta compartilhada.
    """
    assert "[InstallDelete]" in iss_text
    assert re.search(
        r'(?m)^Type:\s*filesandordirs;\s*Name:\s*"\{app\}\\_internal"', iss_text
    ), "o instalador não limpa o payload anterior"
    assert not re.search(
        r'(?m)^Type:\s*filesandordirs;\s*Name:\s*"\{app\}"\s*$', iss_text
    ), "apagar {app} inteiro é largo demais"


def test_both_variants_share_one_appid(iss_text: str) -> None:
    """Mesmo aplicativo, uma instalação: com AppIds diferentes as duas variantes
    apareceriam lado a lado na lista de programas e o `[InstallDelete]` nunca
    veria a pasta da outra."""
    appids = re.findall(r"(?m)^AppId=(.+)$", iss_text)

    assert len(appids) == 1, f"esperava um AppId só, achei {appids}"
    assert "{#Variant}" not in appids[0] and "{#SetupSuffix}" not in appids[0]


# ---------------------------------------------------------------------------
# Descobrir o compilador, e falhar direito sem ele
# ---------------------------------------------------------------------------


def test_the_env_var_wins_over_everything(build_exe, tmp_path, monkeypatch) -> None:
    """Quem instalou o Inno Setup fora do lugar padrão aponta a variável."""
    fake = tmp_path / "ISCC.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv(build_exe.ISCC_ENV_VAR, str(fake))

    assert build_exe.find_iscc() == fake


def test_an_env_var_pointing_nowhere_is_not_silently_ignored(
    build_exe, tmp_path, monkeypatch
) -> None:
    """Apontar a variável para um caminho errado e o build usar outro ISCC qualquer
    seria pior que não achar nenhum: a pessoa acharia que configurou."""
    monkeypatch.setenv(build_exe.ISCC_ENV_VAR, str(tmp_path / "nao-existe.exe"))

    assert build_exe.find_iscc() is None


def test_no_inno_setup_means_no_installer(build_exe, monkeypatch) -> None:
    """Sem o compilador, `--installer` **falha**, e não termina verde sem produzir
    nada. Mesma escolha da §33 e da §47: quem pediu um instalador e recebeu um
    build bem-sucedido sem instalador foi enganado pelo próprio build."""
    monkeypatch.setattr(build_exe, "find_iscc", lambda: None)
    monkeypatch.setattr(build_exe, "dist_dir", lambda light: ROOT)

    with pytest.raises(SystemExit) as raised:
        build_exe.build_installer(light=False)

    assert raised.value.code == 1


def test_the_failure_says_how_to_fix_it(build_exe, monkeypatch, capsys) -> None:
    """A mensagem tem de servir para alguém que nunca ouviu falar de Inno Setup."""
    monkeypatch.setattr(build_exe, "find_iscc", lambda: None)
    monkeypatch.setattr(build_exe, "dist_dir", lambda light: ROOT)

    with pytest.raises(SystemExit):
        build_exe.build_installer(light=False)

    message = capsys.readouterr().err
    assert "jrsoftware.org" in message
    assert build_exe.ISCC_ENV_VAR in message
    # E que não ter instalador não invalida o build que já foi feito.
    assert "não depende disto" in message


def test_a_missing_dist_folder_is_caught_before_the_compiler(
    build_exe, tmp_path, monkeypatch
) -> None:
    """Construir o instalador de uma variante que não foi construída dá um erro do
    ISCC difícil de ler; este é legível e vem antes."""
    monkeypatch.setattr(build_exe, "dist_dir", lambda light: tmp_path / "nao-construida")

    with pytest.raises(SystemExit):
        build_exe.build_installer(light=True)


# ---------------------------------------------------------------------------
# O passo entra no lugar certo do build
# ---------------------------------------------------------------------------


def test_the_installer_runs_after_the_self_test(build_exe) -> None:
    """Comprimir 719 MB leva minutos; fazê-lo antes do auto-teste seria gastá-los
    para empacotar um bundle que o próprio build vai reprovar em seguida."""
    source = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    main_body = source.split("def main()", 1)[1]

    assert main_body.index("smoke_test(") < main_body.index("build_installer(")
