"""Migração do `project_state.json` entre versões de schema (§19.2).

### O problema que isto resolve

`load_project_state` sempre foi tolerante: lê tudo com `.get()` e preenche padrão
para o que faltar. Isso funciona **para trás** e continua valendo — há teste
carregando projeto de schema 1, 2 e 7.

O que não existia era proteção **para frente**. Abrir com este app um projeto
gravado por uma versão futura (schema 9, digamos) funcionava em silêncio: os
campos que este código não conhece eram descartados na leitura, e o primeiro
`Salvar Projeto` — ou o autosave, que roda sozinho a cada 2 minutos — gravava por
cima do arquivo bom sem eles. Perda de trabalho silenciosa, provocada pelo próprio
mecanismo que existe para não perder trabalho.

`migrate_payload` recusa esse caso alto e claro. É a única mudança de comportamento
aqui; todo o resto é registrar o que já acontecia.

### O que a história do projeto de fato registrou

| Schema | O que mudou | Como se sabe |
|---|---|---|
| 1 | sem `side_to_move`, sem padding, sem borda | `tests/test_project_state.py` |
| 2 | `whiteout_padding_pt` uniforme | idem |
| 3–6 | fronteiras não registradas | — |
| 7 | estado do primeiro commit do repositório | `28a21e5` |
| 8 | `candidates` (fila de conferência, §23) | `a82bb98` |

O schema **7 foi mutado no lugar duas vezes** sem trocar de número: `9d1d832`
acrescentou lado a jogar às posições de estudo e `9b51845` os comentários por
lance. Ou seja: para tudo até 7, o número da versão não descreve o formato — quem
descreve é o leitor tolerante de `project_state.py`, e é por isso que aquela
tolerância fica onde está em vez de virar migração aqui.

Deste ponto em diante o contrato é outro: mudou o formato, entra uma função nova
em `_MIGRATIONS` e o número sobe. É o que torna a tabela acima possível de manter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .logging_config import get_logger

logger = get_logger("migrations")

#: Versão que este app grava.
CURRENT_SCHEMA_VERSION = 8

#: Abaixo disto o número da versão não identifica o formato (ver o cabeçalho); a
#: leitura cai na tolerância por campo de `project_state.py`.
OLDEST_DOCUMENTED_SCHEMA = 7


class ProjectSchemaError(RuntimeError):
    """O arquivo não pode ser carregado com segurança por esta versão do app."""


@dataclass(frozen=True)
class MigrationReport:
    from_version: int
    to_version: int
    steps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def migrated(self) -> bool:
        return self.from_version != self.to_version

    def describe(self) -> str:
        if not self.migrated:
            return ""
        detail = "; ".join(self.steps) if self.steps else "sem alterações de conteúdo"
        return f"projeto migrado do schema {self.from_version} para {self.to_version} ({detail})"


def _v7_to_v8(payload: dict) -> str:
    """Schema 8 acrescentou a fila de candidatos (§23).

    Um projeto de schema 7 não tem o campo, e ausente significa fila vazia — não
    significa "não sei", porque antes do 8 não havia como enfileirar nada.
    """
    if "candidates" not in payload:
        payload["candidates"] = []
        return "fila de candidatos criada vazia"
    return "fila de candidatos já presente"


#: Chave = versão de origem; valor = função que a leva para a versão seguinte.
#: Cada função muta `payload` no lugar e devolve uma frase para o log/UI.
_MIGRATIONS: dict[int, Callable[[dict], str]] = {
    7: _v7_to_v8,
}


def _declared_version(payload: dict) -> int:
    raw = payload.get("schema_version", OLDEST_DOCUMENTED_SCHEMA)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("schema_version inválido (%r); assumindo %d", raw, OLDEST_DOCUMENTED_SCHEMA)
        return OLDEST_DOCUMENTED_SCHEMA


def migrate_payload(payload: dict) -> tuple[dict, MigrationReport]:
    """Leva o conteúdo lido do JSON até `CURRENT_SCHEMA_VERSION`.

    Levanta `ProjectSchemaError` quando o arquivo é mais novo que este app: ali
    continuar seria descartar campos desconhecidos e depois gravá-los por cima.
    """
    if not isinstance(payload, dict):
        raise ProjectSchemaError("Arquivo de projeto inválido: esperado um objeto JSON.")

    declared = _declared_version(payload)

    if declared > CURRENT_SCHEMA_VERSION:
        raise ProjectSchemaError(
            f"Este projeto foi gravado no formato {declared}, mais novo que o suportado "
            f"por esta versão do aplicativo ({CURRENT_SCHEMA_VERSION}). Atualize o "
            "aplicativo para abri-lo — carregá-lo agora descartaria informação e o "
            "próximo salvamento a perderia de vez."
        )

    steps: list[str] = []
    version = declared
    if version < OLDEST_DOCUMENTED_SCHEMA:
        # Não há migração passo a passo para 1..6: as fronteiras nunca foram
        # registradas. A tolerância por campo do leitor cobre esses arquivos, e
        # dizer isso no log é melhor que fingir uma cadeia que não existe.
        steps.append(f"formato antigo ({version}) lido pela tolerância por campo")
        logger.info("Projeto em schema %d: lido em modo de compatibilidade", version)
        version = OLDEST_DOCUMENTED_SCHEMA

    while version < CURRENT_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(version)
        if migrate is None:
            raise ProjectSchemaError(
                f"Não há migração registrada do schema {version} para "
                f"{version + 1}. Isto é um defeito do aplicativo."
            )
        detail = migrate(payload)
        steps.append(f"{version}→{version + 1}: {detail}")
        version += 1

    payload["schema_version"] = CURRENT_SCHEMA_VERSION
    report = MigrationReport(
        from_version=declared,
        to_version=CURRENT_SCHEMA_VERSION,
        steps=tuple(steps),
    )
    if report.migrated:
        logger.info("Migração de projeto: %s", report.describe())
    return payload, report
