# Chess PDF Editor

Aplicacao desktop para:
- abrir PDF;
- selecionar diagramas de xadrez;
- reconhecer a posicao **na propria maquina** (ou por API, ou os dois);
- editar/corrigir no tabuleiro;
- substituir no PDF local com overlay HQ;
- salvar/carregar projeto de trabalho.

## Requisitos

- Python 3.10+
- Windows/Linux

## Instalacao

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Opcional para render vetorial de melhor qualidade:

```powershell
pip install cairosvg
```

Opcional (recomendado) para o **reconhecimento local**, sem depender de servidor:

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[local]"
```

Sem esses pacotes o app continua funcionando e usa o servico externo. Com eles,
`Detectar no PDF` roda offline em ~0,6 s por pagina. Veja
[Motor de reconhecimento](#motor-de-reconhecimento).

Opcional para usar fonte Merida no diagrama exportado:

1. Crie a pasta `assets/fonts/`
2. Coloque o arquivo `Merida.ttf` ou `Merida.otf` nela

Alternativa: defina variavel de ambiente `CHESS_MERIDA_FONT` apontando para `.ttf`/`.otf`.

Com fonte Merida configurada, o exportador gera overlay vetorial (PDF) com a fonte embutida no diagrama.
Sem Merida, `cairosvg` habilita fallback vetorial usando `python-chess`.

Opcional para usar imagens de pecas no editor/tabuleiro de estudo:
- O app detecta automaticamente `assets/piece_images` com arquivos como `wp.png`, `wk.png`, `bp.png`, etc.
- Tambem detecta `Python-Easy-Chess-GUI-master/Images/60`.
- Alternativa: defina `CHESS_PIECE_IMAGE_DIR` apontando para uma pasta com `wp.png`/`wk.png` ou `wP.png`/`wK.png`.

## Executar App

```powershell
python scripts/run_app.py
```

Ou, apos instalar pacote:

```powershell
python -m chess_pdf_editor
```

## Fluxo de uso

1. Clique em `Abrir PDF`.
2. Escolha o modo de trabalho na toolbar: `Leitura`, `Estudo` ou `Edicao`.
3. Navegue para a pagina desejada.
4. Desenhe a selecao do diagrama no preview.
5. Clique em `Reconhecer seleção` (usa endpoint configurado).
6. Opcional: clique em `Reconhecer página` ou `Detectar no PDF` para varrer automaticamente.
   Por padrao as deteccoes entram na fila `Candidatos` para voce conferir antes de aplicar
   (veja `Conferir antes de aplicar` abaixo).
   O modo em lote descarta deteccoes que ocupam mais de 50% da pagina (heuristica anti-falso-positivo).
   Se cancelar no meio, o proximo clique retoma da pagina pendente.
7. Corrija a posicao no `Editor de Tabuleiro` se necessario: selecione uma peça na paleta e clique na casa; clique direito limpa a casa.
   O painel `Prévia (antes / depois)` mostra o resultado em tempo real enquanto voce corrige.
8. Se necessário, abra `Aparência` > `Ajustes avançados` para ajustar `Padding whiteout` por lado e `Borda`.
   A opcao `Aplicar em todas as substituicoes` (ligada por padrao) replica a configuracao para toda a lista.
9. Clique em `Adicionar substituição`.
10. Para apagar coordenadas/letras residuais, selecione a area e clique em `Adicionar apagamento`.
    Substituições e apagamentos aparecem juntos na lista `Alterações`.
11. Repita para outros diagramas.
12. Clique em `Exportar PDF`.
   Opcional: na aba `Aparência`, abra `Ajustes avançados` e marque/desmarque `Incluir link Lichess no PDF exportado`.
   Quando habilitado, o PDF exportado inclui um link `Lichess` em azul abaixo de cada diagrama substituido.

Ao iniciar, o app tenta restaurar o ultimo projeto salvo. Se nao houver projeto valido, ele reabre o ultimo PDF usado e usa essa pasta como ponto inicial ao abrir outro PDF.

## Conferir antes de aplicar

Na aba `OCR`, a opcao **`Aplicar automaticamente ao reconhecer página/PDF`** decide o que
`Reconhecer página` e `Detectar no PDF` fazem com o que encontrarem:

- **Desligada (padrao):** as deteccoes entram na secao **`2 · Conferir`** (que so aparece
  quando ha algo na fila), marcadas na pagina com retangulo roxo pontilhado.
  Nada e aplicado ao PDF ainda. Clique em cada candidato
  para carrega-lo no editor — a pagina pula para a pagina certa, a area fica selecionada e
  a posicao vai para o tabuleiro. Com a prévia ligada voce ve exatamente como ficaria.
  Entao use `Aplicar` (ou `Enter`) para confirmar, ou `Descartar` (ou `Delete`) para jogar fora.
  `Aplicar todos` / `Descartar todos` resolvem a lista inteira de uma vez.
- **Ligada:** as substituicoes sao aplicadas direto, como antes. Em `Detectar no PDF` isso
  tambem dispara a exportacao automatica de `<nome>_hq.pdf` ao final.

Se voce corrigir a posicao enquanto confere um candidato, `Aplicar` grava a versao
corrigida — o que esta na tela e o que e aplicado.

Os candidatos pendentes sao salvos no projeto, entao a fila sobrevive a fechar e reabrir o app.

### Revisar so o que esta incerto

Um livro de 898 paginas produz centenas de candidatos em ~8 minutos. Conferir todos
um a um e o que demora — e a maioria esta certa. Dois controles na secao
`2 · Conferir` atacam isso:

- **`So leituras incertas`** esconde os candidatos com confianca acima do limiar
  (padrao `< 0,80`, o mesmo ponto em que o motor hibrido pede segunda opiniao).
  Confianca desconhecida conta como incerta — nao saber nao e o mesmo que estar
  confiante.
- **`Mais incertos primeiro`** ordena por confianca crescente em vez da ordem das
  paginas, para a revisao comecar onde ha chance real de erro.

Com o filtro ligado, os botoes viram `Aplicar visiveis` / `Descartar visiveis` e a
confirmacao diz quantos ficam na fila: uma acao em massa **nunca** toca no que
esta escondido. O titulo da secao mostra `(N incertos de M)`.

## Reconhecimento e exportacao em segundo plano

`Detectar no PDF` e `Exportar PDF` rodam fora da thread da interface. Na pratica:

- a janela continua respondendo e a barra de progresso anda de verdade;
- `Cancelar` e atendido na hora — o lote termina a pagina atual e para, gravando
  o ponto de retomada;
- fechar a janela durante um lote e seguro: o app cancela e espera o worker sair.

O estilo (`Padding`, `Borda`) usado pelo lote e o que estava configurado quando
voce clicou: mudar no meio da execucao nao faz metade dos diagramas sair
diferente da outra metade.

## Motor de reconhecimento

Em `OCR` > `Avancado` > `Motor de reconhecimento` voce escolhe entre tres modos, e
a escolha e lembrada entre sessoes:

| Modo | O que faz | O que sai da maquina |
|---|---|---|
| **Local primeiro, remoto como reforco** (padrao) | reconhece localmente e so consulta o servico externo nas paginas em que a confianca ficar abaixo de 0,80 | so essas paginas |
| **Somente local (offline)** | tudo na sua maquina | **nada** |
| **Somente remoto** | comportamento das versoes anteriores | todas as paginas |

Logo acima do botao `Reconhecer`, um rotulo diz qual e a situacao atual — inclusive
quando o motor local nao esta instalado, e por que.

**Antes do primeiro envio** para o servico externo o app pergunta, dizendo o
destino e quantas paginas estao em jogo. Marque `Nao perguntar de novo neste
computador` para nao ver mais o aviso. No modo `Somente local` ele nunca aparece.

Desempenho medido num livro de 898 paginas, CPU sem placa de video: **0,57 s por
pagina** (0,22 s de render + 0,35 s de deteccao e classificacao), ou ~8,5 min para
o livro inteiro sem tocar a rede.

### Modelo local

O classificador vem em `models/piece_classifier.pt`. Para apontar outro, use
`OCR` > `Avancado` > `Modelo local (.pt)` ou a variavel `CHESS_LOCAL_MODEL`.

O modelo e o detector vem do projeto **ChessVisionOFF_Puro** (3.290 diagramas
reais rotulados). O codigo esta em `src/chess_pdf_editor/local_ocr/_vendor/` como
copia fiel — **nao edite ali**: correcoes vao no projeto de origem e voltam como
recopia.

### Endpoint do OCR

O padrao aparece em `OCR` > `Avancado` > `Endpoint OCR` e a sua escolha e
lembrada entre sessoes. Deixe o campo vazio para voltar ao padrao.

Para scripts e ambientes automatizados, sem tocar na interface:

```powershell
$env:CHESS_OCR_ENDPOINT = "https://meu-servidor/predict"
$env:CHESS_OCR_TIMEOUT  = "60"
```

A confianca da deteccao e guardada junto da substituicao no projeto. No motor
local ela e a confianca da **pior casa** do tabuleiro, nao a media — a media fica
alta mesmo com erro, porque ~77% das casas sao vazias e triviais.

## Ajustar a selecao sem redesenhar

O retangulo desenhado na pagina pode ser corrigido no lugar:

- **arraste uma alca** (cantos e bordas) para redimensionar;
- **arraste o meio** para deslocar o retangulo inteiro;
- **setas do teclado** deslocam 1 pt; com `Shift`, 0,25 pt; com `Ctrl`,
  redimensionam. O passo e sempre em pontos do PDF, qualquer que seja o zoom.

As setas so pertencem a selecao quando existe uma e o visor esta em foco; sem
selecao elas continuam virando pagina.

**`Ajustar selecao a borda` (Ctrl+B)** encosta a selecao nas bordas reais do
tabuleiro. Precisa das dependencias do motor local (so o detector — nao carrega o
classificador). Se nao encontrar borda nenhuma, nada e alterado.

## Auto-orientar a posicao

`Auto-orientar` (Ctrl+Shift+R), no editor de tabuleiro, testa as 4 rotacoes e
aplica a mais plausivel — usando contagem de reis, peoes na 1ª/8ª fila e o sentido
do avanco dos peoes. Se a escolha for apertada, a barra de status avisa para voce
conferir. Quando a posicao ja esta de pe, nada muda.

## Relatorio de alteracoes

`Arquivo` > `Exportar relatorio...` (Ctrl+Shift+E) grava uma linha por alteracao em
**CSV** (para abrir na planilha e ordenar por confianca) ou **JSON** (para
comparar dois processamentos). Cada linha tem pagina, bbox em pontos, largura x
altura, FEN, origem da deteccao, confianca e os avisos de validacao.

O JSON traz ainda um resumo (contagens, confianca minima e media) e qual motor
produziu aquele processamento.

## Correcoes para treino

`Arquivo` > `Exportar correcoes para treino...` grava os diagramas que voce
corrigiu no formato do dataset que treina o motor local: `samples/*.png` (800x800,
recortados do PDF a 300 DPI) e linhas acrescentadas a `labels.csv`.

O arquivo de destino nunca e sobrescrito, so acrescentado. O retreino em si
acontece no projeto ChessVisionOFF_Puro, onde estao os splits e as metricas.

## Prévia ao vivo do resultado

Voce nao precisa exportar o PDF para saber como o diagrama vai ficar.

- **`Prévia do resultado` (Ctrl+D)**, na toolbar ou no menu `PDF`: a pagina passa a
  mostrar o resultado das alteracoes em vez do PDF original. Pressione de novo para voltar.
- **`Prévia (antes / depois)`**, na aba `OCR`: miniaturas lado a lado do diagrama que
  voce esta editando.

A prévia inclui a substituicao **antes de voce clicar em `Adicionar substituição`**:
basta selecionar a area e montar a posicao. Ela acompanha ao vivo:

- pecas movidas no editor de tabuleiro;
- edicao direta do campo FEN;
- `Padding whiteout` por lado e `Borda`;
- `Aplicar whiteout antes do overlay` e `Incluir link Lichess`;
- troca da fonte Merida;
- apagamentos adicionados ou removidos.

Selecionar uma substituicao ja adicionada e edita-la tambem funciona: a prévia mostra a
edicao em andamento, nao a versao salva.

A prévia so desenha uma posicao quando ela pertence de fato a area selecionada. Ao
selecionar um diagrama novo, a posicao do diagrama anterior **nao** e reaproveitada: a
prévia fica em branco ate voce reconhecer a selecao ou montar a posicao no tabuleiro.

A prévia usa exatamente o mesmo codigo da exportacao, entao **o que aparece na tela e o
que vai para o PDF** — isso e verificado por teste automatizado comparando os dois
renders byte a byte. Na prévia as marcacoes de trabalho (retangulos coloridos) somem para
nao atrapalhar a leitura do resultado, e o titulo da janela mostra `[prévia do resultado]`.

Custo medido em um livro de 1120 paginas: ~120 ms por atualizacao, o mesmo que abrir uma
pagina normalmente.

## Modo Estudo (offline)

No toolbar, use `Estudo` para abrir um painel lateral com:
- movimentos legais (click na peca e depois na casa destino);
- seta do ultimo lance;
- lista SAN lateral em arvore (clique em qualquer lance, variante ou subvariante para navegar ate ele);
- `Desfazer` / `Refazer`;
- `Resetar Linha`;
- `Partida inicial` para estudar uma linha de abertura sem OCR/FEN do PDF;
- suporte a variantes e subvariantes: volte a um lance e jogue outra continuacao para criar uma variante;
- `Var. anterior` / `Var. proxima` para alternar entre variantes irmas no mesmo ponto da arvore;
- `Importar PGN`, `Copiar FEN`, `Copiar PGN` e `Salvar PGN`.
  Ao importar PGN, os comentarios da linha principal sao preservados nos lances correspondentes.

Para estudar uma posicao do PDF:
1. Selecione o diagrama na pagina.
2. Reconheca ou monte/corrija a FEN no editor.
3. Ajuste `Vez de jogar` na aba `FEN` quando a posicao começar com as pretas.
4. Clique em `Estudar selecao`.
5. Clique no lance desejado na lista SAN e use `Atualizar linha` para salvar o PGN estudado naquela posicao.
   Os comentarios `antes` e `depois` ficam gravados por lance selecionado e sao preservados ao navegar pela linha.
   O painel mostra `Comentando: ...` para indicar o lance ativo dos campos de comentario.
   A linha e os comentarios tambem sao sincronizados automaticamente ao navegar pelos lances, copiar ou salvar PGN.

As posicoes de estudo ficam em `Posicoes deste PDF` e sao salvas no projeto.
Para livros de abertura, use `Partida inicial` para criar uma entrada de estudo a partir da posicao inicial do xadrez.

## Desfazer e refazer

No modo `Edicao`, `Ctrl+Z` desfaz e `Ctrl+Y` refaz. O historico cobre
substituicoes, apagamentos e a fila de candidatos — inclusive as acoes em massa
como `Descartar todos`, que antes eram definitivas. O menu `Editar` mostra o que
sera desfeito (`Desfazer remover substituicao`).

Ajustes de `Padding`/`Borda` entram no historico como uma unica etapa por
sequencia de ajustes, e nao um passo por clique no spinbox.

No modo `Estudo`, `Ctrl+Z` continua pertencendo a linha de lances.

## Projeto (checkpoint) e autosave

- `Salvar Projeto`: salva operacoes pendentes em JSON.
- `Carregar Projeto`: restaura operacoes e pagina atual.

O trabalho tambem e salvo sozinho, a cada 2 minutos e ao fechar a janela:

- se voce ja escolheu um arquivo de projeto, o autosave grava nele;
- se ainda nao escolheu, grava em
  `%LOCALAPPDATA%\ChessPdfEditor\autosave\<nome do PDF>-<hash>.autosave.json`
  (Linux: `~/.local/state/ChessPdfEditor/autosave`), sem espalhar arquivos pelas
  suas pastas de livros.

Na sessao seguinte o app reencontra esse arquivo sozinho e continua de onde
parou. Em `Configuracoes` voce pode desligar o autosave ou forcar
`Salvar agora`. A gravacao e atomica: um autosave interrompido no meio nao
corrompe o projeto anterior.

## Scripts uteis

Verificar ambiente:

```powershell
python scripts/check_env.py
```

Coletar pseudo-labels para treino usando API OCR:

```powershell
python scripts/collect_api_labels.py --input .\imagens --output .\data\labels.jsonl
```

Aplicar um projeto salvo em batch:

```powershell
python scripts/batch_replace.py --project .\project_state.json --output .\saida.pdf
```

Gerar dataset offline (imagens + FEN) a partir de projetos salvos:

```powershell
python scripts/collect_project_labels.py --projects .\project_state.json --images-dir .\data\images --labels .\data\labels.jsonl --square --size 512
```

## Estrutura principal

```text
src/chess_pdf_editor/
  app.py              # GUI principal (janela, modos, previa ao vivo)
  widgets.py          # viewer selecionavel (alcas/teclado), editor, antes/depois
  pdf_service.py      # render, previa e overlay no PDF
  recognition.py      # escolha do motor: local, remoto ou hibrido
  local_ocr/          # reconhecimento local (detector + classificador)
    engine.py         #   adaptador para o contrato de OCR do app + snap
    _vendor/          #   copia fiel do ChessVisionOFF_Puro — nao editar
  ocr_api.py          # cliente da API OCR (endpoint/timeout/confianca)
  workers.py          # OCR em lote e exportacao em segundo plano
  orientation.py      # auto-orientacao por plausibilidade da posicao
  report.py           # relatorio de alteracoes em CSV/JSON
  feedback.py         # correcoes exportadas para o dataset de treino
  history.py          # pilha de desfazer/refazer do modo Edicao
  autosave.py         # caminho e gravacao atomica do autosave
  logging_config.py   # log em arquivo com rotacao
  fen.py              # utilitarios e validacoes FEN
  renderer.py         # render do diagrama (PDF/PNG)
  study.py            # arvore de lances/variantes do modo Estudo
  project_state.py    # persistencia de checkpoint
models/
  piece_classifier.pt # classificador das 64 casas usado pelo motor local
```

## Testes

```powershell
python -m pytest tests -q
```

Os testes de interface rodam com Qt em modo `offscreen` e usam um arquivo de
configuracao temporario, entao nao abrem janelas nem alteram suas preferencias.
O autosave e o log tambem sao redirecionados para um diretorio temporario, entao
a suite nao escreve nada em `%LOCALAPPDATA%`.

Os testes do motor local (`tests/test_local_ocr.py`) pulam sozinhos quando as
dependencias opcionais nao estao instaladas.

A suite roda automaticamente em push e pull request (GitHub Actions, Windows e
Ubuntu) — veja `.github/workflows/tests.yml`. Um segundo job instala o extra
`local` e falha se o motor local nao ficar disponivel, para um skip silencioso nao
passar por verde.

## Desenvolvimento

### Ambiente

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[local,dev]"
```

O `-e` (editavel) e o que faz os testes e o app usarem o codigo de `src/` sem
reinstalar a cada mudanca.

### Rodar

```powershell
python scripts/run_app.py
```

Verificacao rapida de que o app se acha por dentro (assets, modelo, janela), sem
abrir janela e sem tocar suas preferencias:

```powershell
python scripts/run_app.py --self-test
```

### Testes e lint

```powershell
python -m pytest -q
```

```powershell
python -m ruff check src tests --select F --exclude "src/chess_pdf_editor/local_ocr/_vendor"
```

`_vendor/` fica de fora do lint porque e copia fiel de outro projeto — ver abaixo.

### Executavel Windows

```powershell
pip install -e ".[build]"
python scripts/build_exe.py
```

O script confere o ambiente **antes** de construir (dependencias e modelo no
lugar), constroi via `packaging/chess_pdf_editor.spec` e depois **abre o
executavel gerado** com `--self-test`, a partir de outra pasta de trabalho — e o
que pega o erro classico de empacotamento, um caminho que so funcionava rodando
do repositorio.

Sai em `dist/ChessPdfEditor/`. E `--onedir`, nao `--onefile`: com torch e Qt
dentro, o `--onefile` extrairia ~2 GB a cada abertura.

Opcoes: `--check` (so confere), `--no-clean` (reaproveita `build/`),
`--skip-smoke`.

### Codigo de terceiros

`src/chess_pdf_editor/local_ocr/_vendor/` e copia fiel do projeto
ChessVisionOFF_Puro (detector + classificador). **Nao edite ali**: correcao vai no
projeto de origem e volta como recopia, para `diff` continuar sendo a forma de
saber o que mudou. A proveniencia esta em `_vendor/__init__.py`.

### Formato do projeto salvo

`project_state.json` tem `schema_version`. Ao mudar o formato:

1. escreva a funcao de migracao em `src/chess_pdf_editor/migrations.py`;
2. registre-a em `_MIGRATIONS` sob a versao de origem;
3. suba `CURRENT_SCHEMA_VERSION`;
4. acrescente a linha na tabela do cabecalho do modulo.

Um projeto gravado por versao mais nova que o app e **recusado** com mensagem
explicita — carrega-lo descartaria campos e o autosave gravaria a perda por cima.

### Commits e releases

- Mensagem no imperativo, descrevendo o efeito para o usuario, nao os arquivos.
- Corpo explica **por que**, com os numeros medidos quando houver.
- Um sprint do plano por commit, quando as mudancas forem interdependentes.
- O plano tecnico (`plano_editor_diagramas_xadrez_pdf.md`) e atualizado no mesmo
  commit que a implementacao — tabela de estado, roadmap e a secao do sprint.

## Logs

Falhas de render, exportacao, OCR e carregamento de projeto sao registradas em:

- Windows: `%LOCALAPPDATA%\ChessPdfEditor\logs\chess_pdf_editor.log`
- Linux: `~/.local/state/ChessPdfEditor/logs/chess_pdf_editor.log`

O arquivo rotaciona em 2 MB (3 backups). `Configuracoes` > `Abrir pasta de logs`
leva direto ate ele. `CHESS_PDF_EDITOR_LOG_DIR` muda o destino e
`CHESS_PDF_EDITOR_LOG_LEVEL` (`DEBUG`, `INFO`, ...) muda o detalhamento.

## Observacoes

- O reconhecimento roda localmente por padrao; o servico externo e reforco, nao requisito.
- O fallback de renderizacao funciona sem `cairosvg`.
- `Padding whiteout` por lado (esq/topo/dir/base) e `Borda` sao salvos por substituicao no projeto.
- `Apagamentos` sao salvos separadamente e aplicados antes dos overlays.
- Plano tecnico detalhado: `plano_editor_diagramas_xadrez_pdf.md`.
