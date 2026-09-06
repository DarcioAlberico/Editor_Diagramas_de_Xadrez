"""Cópia em JSON de cada reconhecimento, ao lado do PDF (§55).

### O que faltava

O autosave (§Sprint 5.3) já promete "nunca perder trabalho", mas promete de um
jeito que não cobre o medo concreto de quem reconhece um livro:

* ele grava **um** arquivo por livro, e sobrescreve. O reconhecimento de ontem
  não existe mais depois do de hoje;
* ele grava num diretório do app, com nome derivado de um hash. Achar o arquivo
  para mexer nele à mão é possível, mas ninguém faz isso no susto;
* ele grava **o estado atual**. Descartar a fila de candidatos por engano e
  esperar dois minutos apaga a detecção do disco também — pelo próprio mecanismo
  que existe para não perder nada.

Reconhecer 898 páginas leva ~8 minutos de máquina e mais de uma hora de
conferência. Este módulo grava, a cada reconhecimento, um arquivo que **ninguém
sobrescreve**, na pasta do livro, com data e hora no nome.

### Por que o arquivo é um projeto, e não uma lista de detecções

Um JSON com só as detecções seria um registro para ler, não para usar: para
recuperar o trabalho o usuário teria de digitar de volta, ou o app precisaria de
um importador novo, com o seu próprio conjunto de decisões (o que fazer com o que
já existe? mesclar? substituir?).

O que se grava aqui é o **projeto inteiro** no instante seguinte ao
reconhecimento — o mesmo formato de `Salvar projeto`. Recuperar é
`Arquivo` > `Carregar projeto` e apontar para o arquivo. Nada de novo para
aprender, e nada de novo para manter.

O que a detecção acrescenta é um bloco `reconhecimento` no topo, com o que o
formato de projeto não guarda: qual botão gerou aquilo, que páginas foram
varridas, quantas detecções entraram e se o lote foi cancelado no meio. Ele é uma
chave a mais no JSON, e o leitor de projeto ignora chaves que não conhece — então
o arquivo continua carregando como projeto normal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .autosave import write_project_atomically
from .logging_config import get_logger
from .project_state import ProjectState

logger = get_logger("recognition_snapshot")

#: Aparece no nome do arquivo, entre o nome do livro e a data.
SNAPSHOT_INFIX = "reconhecimento"

#: A chave que o bloco de metadados ocupa no JSON.
METADATA_KEY = "reconhecimento"

#: De onde veio o reconhecimento.
KIND_PAGE = "pagina"
KIND_BOOK = "livro"

#: Para onde as detecções foram.
TARGET_OPERATIONS = "substituicoes"
TARGET_CANDIDATES = "candidatos"

#: Teto de desempate de nome. Dois reconhecimentos no mesmo segundo são raros
#: (o de página leva ~0,6 s), mas dois arquivos com o mesmo nome fariam o segundo
#: apagar o primeiro — que é exatamente o que este módulo existe para evitar.
MAX_NAME_ATTEMPTS = 100


@dataclass(frozen=True)
class RunInfo:
    """O que aconteceu num reconhecimento, para o bloco de metadados."""

    origem: str
    destino: str
    encontrados: int
    paginas: str
    ignorados: int = 0
    grandes_descartadas: int = 0
    falhas: int = 0
    cancelado: bool = False
    motor: str = ""

    def as_payload(self, quando: datetime) -> dict[str, object]:
        return {
            "quando": quando.isoformat(timespec="seconds"),
            "origem": self.origem,
            "destino": self.destino,
            "paginas": self.paginas,
            "encontrados": int(self.encontrados),
            "ignorados": int(self.ignorados),
            "grandes_descartadas": int(self.grandes_descartadas),
            "falhas": int(self.falhas),
            "cancelado": bool(self.cancelado),
            "motor": self.motor,
        }


def snapshot_path(
    pdf_path: str,
    kind: str,
    quando: Optional[datetime] = None,
    directory: Optional[Path] = None,
) -> Path:
    """Onde gravar, sem nunca escolher um nome que já existe.

    O nome do livro fica na frente porque é por ele que o usuário procura, e sai
    intacto: o arquivo vai para a **mesma pasta** do PDF, então tudo o que era
    nome de arquivo válido ali continua sendo.
    """
    pdf = Path(pdf_path)
    base_dir = Path(directory) if directory is not None else pdf.parent
    stamp = (quando or datetime.now()).strftime("%Y%m%d-%H%M%S")
    stem = f"{pdf.stem}-{SNAPSHOT_INFIX}-{kind}-{stamp}"

    candidate = base_dir / f"{stem}.json"
    suffix = 2
    while candidate.exists() and suffix <= MAX_NAME_ATTEMPTS:
        candidate = base_dir / f"{stem}-{suffix}.json"
        suffix += 1
    # Cem arquivos no mesmo segundo é outro problema; gravar por cima do centésimo
    # é menos ruim que recusar a gravar e perder a detecção inteira.
    return candidate


def write_snapshot(
    pdf_path: str,
    state: ProjectState,
    run: RunInfo,
    quando: Optional[datetime] = None,
    directory: Optional[Path] = None,
) -> Path:
    """Grava o projeto mais o bloco do reconhecimento. Devolve o caminho.

    A gravação é a atômica do autosave (§43): temporário mais `os.replace`, com
    `fsync` antes. Vale a mesma razão daquele sprint, e aqui com mais força — o
    arquivo é gravado logo depois de um lote de oito minutos, e um JSON truncado
    no lugar dele seria perder duas vezes.
    """
    destino = snapshot_path(pdf_path, run.origem, quando=quando, directory=directory)
    write_project_atomically(
        str(destino), state, extra={METADATA_KEY: run.as_payload(quando or datetime.now())}
    )
    logger.info(
        "Reconhecimento gravado em %s (%d detecção(ões), páginas %s)",
        destino,
        run.encontrados,
        run.paginas,
    )
    return destino
