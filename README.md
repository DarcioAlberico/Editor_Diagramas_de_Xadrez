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
   Cada reconhecimento com resultado grava um JSON na pasta do PDF, para nada se perder
   (veja [Copia em JSON de cada reconhecimento](#copia-em-json-de-cada-reconhecimento)).
   Por padrao as deteccoes entram na fila `Candidatos` para voce conferir antes de aplicar
   (veja `Conferir antes de aplicar` abaixo).
   O modo em lote descarta deteccoes que ocupam mais de 50% da pagina (heuristica anti-falso-positivo).
   Se cancelar no meio, o proximo clique retoma da pagina pendente.
7. Corrija a posicao no `Editor de Tabuleiro` se necessario: selecione uma peça na paleta e clique na casa; clique direito limpa a casa.
   O painel `Prévia (antes / depois)` mostra o resultado em tempo real enquanto voce corrige.
8. Se necessário, abra a aba `Ajustes` > `Aparência do diagrama` para ajustar `Padding whiteout` por lado e `Borda`.
   A opcao `Aplicar em todas as substituicoes` (ligada por padrao) replica a configuracao para toda a lista.
9. Clique em `Adicionar substituição`.
10. Para as coordenadas do diagrama original, prefira a opcao automatica (veja
    [Coordenadas do diagrama original](#coordenadas-do-diagrama-original)).
    Para qualquer outro resto, selecione a area e clique em `Adicionar apagamento`.
    Substituições e apagamentos aparecem juntos na lista `Alterações`.
11. Repita para outros diagramas.
12. Clique em `Exportar PDF`.
   Opcional: na aba `Ajustes`, abra `Aparência do diagrama` e marque/desmarque `Link Lichess por padrão`.
   Quando habilitado, o PDF exportado inclui um link `Lichess` em azul abaixo de cada diagrama substituido.
   Essa caixa manda so em quem nao escolheu: cada diagrama pode pedir ou recusar o link
   por conta propria, pela galeria (veja [Galeria de diagramas](#galeria-de-diagramas)).

Ao iniciar, o app tenta restaurar o ultimo projeto salvo. Se nao houver projeto valido, ele reabre o ultimo PDF usado e usa essa pasta como ponto inicial ao abrir outro PDF.

## Conferir antes de aplicar

Na aba `Ajustes` > `Reconhecimento`, a opcao **`Aplicar sem conferir`** decide o que
`Reconhecer página` e `Detectar no PDF` fazem com o que encontrarem:

- **Desligada (padrao):** as deteccoes entram na aba **`Conferir`** (que so aparece
  quando ha algo na fila, e traz a contagem no titulo: `Conferir (12)`),
  marcadas na pagina com retangulo roxo pontilhado.
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
um a um e o que demora — e a maioria esta certa. Dois controles na aba
`Conferir` atacam isso:

- **`So leituras incertas`** esconde os candidatos com confianca acima do limiar
  (padrao `< 0,80`, o mesmo ponto em que o motor hibrido pede segunda opiniao).
  Confianca desconhecida conta como incerta — nao saber nao e o mesmo que estar
  confiante.
- **`Mais incertos primeiro`** ordena por confianca crescente em vez da ordem das
  paginas, para a revisao comecar onde ha chance real de erro.

Com o filtro ligado, os botoes viram `Aplicar visiveis` / `Descartar visiveis` e a
confirmacao diz quantos ficam na fila: uma acao em massa **nunca** toca no que
esta escondido. O titulo da secao mostra `(N incertos de M)`.

## Posicoes impossiveis

A validacao da FEN confere a **escrita**: 8 fileiras, 8 casas por fileira,
caracteres validos. Uma leitura de OCR pode passar por isso e ainda assim descrever
uma posicao que **nao pode ter existido**. O app audita a legalidade e avisa:

- **`impossivel:`** — a posicao nao poderia ter surgido de um jogo. Exemplos: tres
  damas com os oito peoes em casa (cada dama extra exige uma promocao, e promover
  gasta um peao), nove peoes, reis encostados, peao na 1ª ou 8ª fila.
- **`suspeita:`** — possivel, mas do tipo que costuma ser erro de leitura. Exemplo:
  tres bispos com peoes faltando — legal, mas incomum.

Um caso merece explicacao: **o rei de quem nao esta a jogar em xeque**. Isso e
impossivel, mas um diagrama de livro quase nunca diz de quem e a vez, e o app
preenche `brancas` por padrao. Entao a auditoria testa os **dois** lados: se a
posicao fica legal com o outro lado a jogar, o aviso e `suspeita: ... o lado a jogar
provavelmente esta trocado` — e nao uma acusacao de impossibilidade.

Onde os avisos aparecem:

- no **rotulo de avisos** da aba `FEN`, ao vivo, enquanto voce edita;
- na coluna `avisos` do **relatorio** (CSV/JSON), prefixados, para auditar um livro
  inteiro na planilha;
- na **fila de revisao**: uma posicao impossivel entra na fila **mesmo com confianca
  alta**, marcada com `⚠ impossivel`. O motor pode estar seguro de uma leitura que
  nao existe, e e justamente essa que ninguem deve aplicar sem olhar. Lado a jogar
  trocado, por ser so suspeita, **nao** entra na fila por si — senao todo diagrama
  com xeque cairia la e o filtro nao filtraria nada.

## Reconhecimento e exportacao em segundo plano

`Detectar no PDF` e `Exportar PDF` rodam fora da thread da interface. Na pratica:

- a janela continua respondendo e a barra de progresso anda de verdade;
- `Cancelar` e atendido na hora — o lote termina a pagina atual e para, gravando
  o ponto de retomada;
- fechar a janela durante um lote e seguro: o app cancela e espera o worker sair.

A exportacao tambem tem `Cancelar`, e a barra conta **paginas alteradas** (num
livro de 898 paginas com 60 diagramas, o total e 60). Cancelar significa
**nenhum arquivo**: a gravacao e o ultimo passo, entao interromper nao deixa PDF
pela metade — e um arquivo exportado antes fica intacto.

O estilo (`Padding`, `Borda`) usado pelo lote e o que estava configurado quando
voce clicou: mudar no meio da execucao nao faz metade dos diagramas sair
diferente da outra metade.

## Copia em JSON de cada reconhecimento

Todo `Reconhecer página` e todo `Detectar no PDF` que **encontra alguma coisa**
grava, na mesma pasta do PDF, um arquivo assim:

```text
Taticas Basicas-reconhecimento-pagina-20260906-053622.json
Taticas Basicas-reconhecimento-livro-20260906-061140.json
```

Nada e sobrescrito: o nome leva data e hora, e dois reconhecimentos no mesmo
segundo ganham um sufixo (`-2`, `-3`). Reconhecer de novo, descartar a fila de
candidatos por engano ou fechar o app sem salvar **nao apaga** o que ja foi
gravado.

O arquivo e um **projeto**, no mesmo formato de `Salvar projeto`. Para recuperar:
`Arquivo` > `Carregar projeto` e aponte para ele. Nao ha importador novo nem
formato novo para aprender.

Alem do projeto, ele carrega um bloco `reconhecimento` dizendo de onde aquilo
veio — o que o formato de projeto sozinho nao guarda:

```json
"reconhecimento": {
  "quando": "2026-09-06T06:11:40",
  "origem": "livro",
  "destino": "candidatos",
  "paginas": "1-898",
  "encontrados": 312,
  "ignorados": 7,
  "grandes_descartadas": 3,
  "falhas": 1,
  "cancelado": false,
  "motor": "hybrid"
}
```

`origem` diz qual botao gerou o arquivo (`pagina` ou `livro`) e `destino` diz se
as deteccoes foram aplicadas direto (`substituicoes`) ou para a fila
(`candidatos`). Um lote **cancelado no meio grava igual** — e o caso que mais
importa, porque quem para na pagina 400 tem 400 paginas de trabalho para nao
perder.

Para desligar: aba `Ajustes` > `Reconhecimento` > `Salvar JSON de cada
reconhecimento`. Se a gravacao falhar (pasta so de leitura, disco cheio), o
reconhecimento **nao e perdido**: a barra de status avisa que o JSON nao foi
gravado e as deteccoes continuam na tela.

### Por que isso nao e o autosave

O autosave existe e continua valendo, mas nao cobre este medo:

| | Autosave | JSON do reconhecimento |
|---|---|---|
| Quantos arquivos | um por livro, sobrescrito | um por reconhecimento |
| Onde | pasta do app, nome com hash | pasta do PDF, nome do livro |
| Quando | a cada 2 min e ao fechar | logo apos cada reconhecimento |
| Descartar a fila por engano | o proximo autosave apaga do disco | o arquivo continua la |

## Motor de reconhecimento

Em `Ajustes` > `Reconhecimento` > `Motor de reconhecimento` voce escolhe entre tres modos, e
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
`Ajustes` > `Reconhecimento` > `Modelo local (.pt)` ou a variavel `CHESS_LOCAL_MODEL`.

O modelo e o detector vem do projeto **ChessVisionOFF_Puro** (3.290 diagramas
reais rotulados). O codigo esta em `src/chess_pdf_editor/local_ocr/_vendor/` como
copia fiel — **nao edite ali**: correcoes vao no projeto de origem e voltam como
recopia.

### Endpoint do OCR

O padrao aparece em `Ajustes` > `Reconhecimento` > `Endpoint OCR` e a sua escolha e
lembrada entre sessoes. Deixe o campo vazio para voltar ao padrao.

Para scripts e ambientes automatizados, sem tocar na interface:

```powershell
$env:CHESS_OCR_ENDPOINT = "https://meu-servidor/predict"
$env:CHESS_OCR_TIMEOUT  = "60"
```

A confianca da deteccao e guardada junto da substituicao no projeto. No motor
local ela e a confianca da **pior casa** do tabuleiro, nao a media — a media fica
alta mesmo com erro, porque ~77% das casas sao vazias e triviais.

## Selecionar um diagrama com um clique

**Clique dentro de um tabuleiro na pagina** e a selecao pula para as bordas dele —
sem arrastar nada. Se houver dois diagramas na pagina, clique no outro para trocar.

- Clicar **fora** de qualquer tabuleiro nao faz nada (antes, um clique perdido
  limpava a selecao). Um clique que raspou a borda por fora ainda conta.
- A deteccao roda no proprio clique: ~40 ms para a pagina inteira em zoom 2,0.
- Precisa das dependencias do motor local (so o **detector** — nao carrega o
  classificador). Sem elas, o clique volta a fazer o que fazia.
- Para desligar: `Ajustes` > `Reconhecimento` > `Clique unico detecta o diagrama`.

Detectar a area **nao** carrega posicao nenhuma: a selecao aparece e o editor
continua como estava, ate voce usar `Reconhecer selecao` ou montar a posicao. E de
proposito — herdar a FEN do diagrama anterior desenharia a posicao errada sobre o
diagrama novo.

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
do avanco dos peoes. Quando a posicao ja esta de pe, nada muda.

A barra de status diz o angulo **e em que a decisao se baseou** (por exemplo, `peoes
apontam o sentido oposto (+2,5 filas)`), porque essa e a informacao que permite a voce
discordar. **Apertar Ctrl+Shift+R de novo desfaz o giro**, desde que voce nao tenha
mexido na posicao no meio — se mexeu, o desfazer e abandonado em vez de apagar sua
edicao.

O desfazer existe por um motivo concreto: a heuristica **erra com confianca** numa
familia que livro de xadrez tem de sobra — o estudo em que os peoes dos dois lados ja
passaram uns pelos outros. Nessas posicoes ela le o diagrama como invertido sem marcar
a escolha como apertada. E tambem por isso o app **nao** avisa sozinho que um diagrama
"parece invertido": o aviso gritaria em cima de leituras corretas.

## Relatorio de alteracoes

`Arquivo` > `Exportar relatorio...` (Ctrl+Shift+E) grava uma linha por alteracao em
**CSV** (para abrir na planilha e ordenar por confianca) ou **JSON** (para
comparar dois processamentos). Cada linha tem pagina, bbox em pontos, largura x
altura, FEN, origem da deteccao, confianca e os avisos de validacao.

O JSON traz ainda um resumo (contagens, confianca minima e media) e qual motor
produziu aquele processamento.

## Exportar diagramas isolados

`Arquivo` > `Exportar diagramas isolados...` grava **um arquivo por substituicao**,
para reaproveitar a posicao num slide, numa lista de exercicios ou num post.

| Formato | Desenho | Quando usar |
|---|---|---|
| `PNG` | o mesmo do PDF exportado (Merida) | colar em slide, documento, post |
| `PDF` | o mesmo do PDF exportado (Merida vetorial) | imprimir, incluir em LaTeX |
| `SVG` | do `python-chess` | editar o vetor em outro programa |

O `SVG` e o unico cujo desenho **nao** e o que vai para o PDF do livro. E de proposito:
quem exporta SVG quer caminhos editaveis, e nao glifos de uma fonte que o outro
programa talvez nao tenha.

Os nomes sao `diagrama-pag0012-02.png` — pagina com zeros a esquerda (para a pasta
ordenar certo) e a ordem na pagina, de cima para baixo. Junto vem um `indice.csv` com
arquivo, pagina, FEN, lado a jogar e origem de cada um.

A exportacao roda em segundo plano com barra de progresso (~35 ms por PNG, ~31 ms por
PDF, ~1 ms por SVG). **Cancelar mantem os arquivos ja gravados** — ao contrario da
exportacao do PDF, aqui sao arquivos independentes e os prontos servem por si; o aviso
final diz quantos ficaram de fora. Um diagrama que falhe nao interrompe os outros.

## Correcoes para treino

`Arquivo` > `Exportar correcoes para treino...` grava os diagramas que voce
corrigiu no formato do dataset que treina o motor local: `samples/*.png` (800x800,
recortados do PDF a 300 DPI) e linhas acrescentadas a `labels.csv`.

O arquivo de destino nunca e sobrescrito, so acrescentado. O retreino em si
acontece no projeto ChessVisionOFF_Puro, onde estao os splits e as metricas.

## Galeria de diagramas

`Diagramas` > `Galeria de diagramas` (Ctrl+G) abre uma grade com **todos** os
diagramas do livro em miniatura, antes e depois lado a lado. A maior parte das
paginas de um livro nao tem diagrama nenhum; a galeria mostra so o que interessa.

- Clique numa miniatura e a janela principal vai ate aquele diagrama — pagina,
  selecao e posicao carregadas. A galeria continua aberta ao lado.
  (Ctrl+clique e Shift+clique **nao** navegam: sao gestos de selecao.)
- Candidatos aparecem junto das substituicoes, marcados como tal, com a confianca.
- O "depois" mostra a **pagina inteira aplicada**, apagamentos inclusive: e o que o
  PDF exportado vai conter.

As miniaturas sao renderizadas fora da thread da interface e vao aparecendo na
grade; fechar a janela no meio cancela o trabalho. Medido num livro real: ~128 ms
por diagrama (44 diagramas em 5,6 s).

Para conferir **um** diagrama de perto — lendo o numero do lance na pagina, com os
campos ao lado — veja [Navegador de diagramas](#navegador-de-diagramas).

### Ajustar um diagrama sem sair da galeria

O rodape edita o diagrama selecionado, e o que voce mexe ali vale **so para ele**:

| Campo | O que faz |
|---|---|
| `Vez de jogar` | brancas ou pretas |
| `Lance` | o numero do lance |
| `Link Lichess` | `Padrão`, `Com link` ou `Sem link` |
| `Borda` | a espessura da borda, em pt |

A edicao entra na hora e **refaz a miniatura**, entao o "depois" nunca fica
afirmando um resultado que deixou de valer. Cada edicao e um passo de `Ctrl+Z`.

`Link Lichess` tem tres estados de proposito. `Padrão` deixa a caixa
`Link Lichess por padrão` (aba `Ajustes`) valer; os outros dois **vencem** a caixa
global, nos dois sentidos. Por isso a caixa global manda so em quem nao escolheu:
desmarca-la nao tira o link de um diagrama que pediu para te-lo. A legenda da celula
marca so quem discorda da global (`· com link`, `· sem link`) — numa grade de
centenas, o que se procura e a excecao.

### Aplicar a varios de uma vez

A grade aceita selecao multipla (Ctrl, Shift, Ctrl+A). Com dois ou mais
selecionados o rodape **para de gravar ao vivo** e vira um formulario: o titulo passa
a `N diagramas selecionados` e aparece a linha do lote.

Sao dois gestos explicitos, e nessa ordem:

1. escolher os diagramas;
2. marcar **quais campos** o lote toca (`Vez`, `Lance`, `Link`, `Borda`).

Nenhuma caixa vem marcada, entao o botao nasce desabilitado — e diz o numero:
`Aplicar aos 12`. Um lote inteiro e **um** passo de `Ctrl+Z`, nao doze.

`Link` e `Borda` sao o motivo da linha existir: sao as escolhas que valem para um
capitulo inteiro. `Lance` esta ali por simetria, mas cada diagrama tem o seu.

### Filtrar a grade

Tres recortes na barra de cima, para pegar **um pedaco** sem rolar e Shift+clicar por
90 paginas: **faixa de paginas** (o capitulo), **tipo** (substituicoes x candidatos) e
**escolha de link** (para achar as excecoes). `Mostrar tudo` limpa os tres.

- Uma faixa invertida e lida como voce quis: "40 a 12" e 12 a 40.
- O filtro e de **vista**, nao de trabalho: as miniaturas de todos continuam sendo
  renderizadas.
- Filtrar **deseleciona** o que esconde, e um lote nunca toca no que esta escondido.
  Quando sobra algo de fora, o aviso diz quantos: `4 fora do filtro não foram tocados`.
- O filtro nao se reaplica sozinho depois de uma edicao — filtrar por "sem link" e
  entao marcar "padrao" faria a selecao sumir no instante do clique. A legenda se
  atualiza no lugar.

## Navegador de diagramas

`Diagramas` > `Navegador de diagramas` (Ctrl+Shift+G) mostra **um** diagrama por
vez, no maior tamanho que a janela der: a esquerda como ele esta no PDF, a direita
como vai ficar. E a janela para conferir as **etiquetas** — o numero do lance e a
vez de jogar, que o livro imprime em corpo pequeno em volta do tabuleiro e que a
miniatura da galeria nao tem tamanho para mostrar.

- `Anterior` / `Proximo` (Alt+← e Alt+→) andam na fila, na ordem de leitura do
  livro. O campo do meio pula direto para o n-esimo diagrama.
- Ele **abre no diagrama que ja estava selecionado** na janela principal.
- Candidatos entram na mesma fila, marcados como `ainda nao aplicado` e com a
  confianca.
- `Ir para este diagrama` leva a janela principal ate ele (pagina, selecao e
  posicao carregadas) sem fechar o navegador — e la que se corrige a posicao,
  aplica um candidato ou remove.

Os campos sao os mesmos da galeria (`Vez de jogar`, `Numero do lance`,
`Link Lichess`, `Borda`) e valem so para o diagrama a vista. Uma mexida vira **um**
passo de `Ctrl+Z`, mesmo arrastando o spinbox por dez valores.

### Por que ele mostra a FEN, o link e a legalidade

`Vez de jogar` e `Numero do lance` **nao mudam um pixel** do tabuleiro desenhado:
o que sai deles no PDF e a FEN do link Lichess, mais o relatorio e a exportacao de
diagramas isolados. Uma janela que mostrasse so as duas imagens deixaria o campo
mais importante sem retorno nenhum. Por isso, abaixo dos campos:

| Linha | O que diz |
|---|---|
| `FEN final` | a string exata que vai para o link, o relatorio e a exportacao |
| Link | se o PDF vai levar o link (e o endereco, para conferir a posicao) |
| Legalidade | a auditoria da §37, com o lado a jogar que voce escolheu |

A linha da legalidade e o unico juiz automatico do campo `Vez de jogar`: quando a
posicao so fica ilegal com o lado indicado, ela diz `o lado a jogar provavelmente
esta trocado` — que e o erro mais comum, ja que o app preenche `brancas` por padrao.

## Experimentar um estilo no livro inteiro

`Aplicar em todas as substituicoes` (aba `Ajustes`) esta ligado por padrao: mexer
no padding ou na borda reescreve o estilo de **todas** as substituicoes na hora.
Voce ve o efeito na pagina aberta; nas outras, nao.

`Diagramas` > `Experimentar estilo em todas...` — ou o botao `Experimentar em
todas...` na aba `Ajustes` — abre uma grade onde cada celula tem o **estilo atual
a esquerda e o proposto a direita**, em diagramas de todo o livro:

- os spinboxes da janela ajustam a proposta, e a grade se atualiza sozinha;
- **nada muda** enquanto a janela esta aberta. O botao diz quantas substituicoes
  seriam afetadas de verdade (`Aplicar em 37 de 42`), e `Aplicar (nada muda)` quando
  a proposta e igual ao que ja esta salvo;
- aplicar vale para **todas** as substituicoes do livro e cabe num unico Ctrl+Z.

Livro grande nao entra inteiro na grade: ela mostra uma amostra de 24 diagramas
**espalhados pelo livro** — nao os 24 primeiros, que num livro costumam ser todos do
mesmo capitulo — e diz na tela quantos de quantos.

A grade serve para pegar problema grosso: borda que encostou no texto, padding que
comeu a legenda, diagrama fora do padrao dos outros. Para acertar fracao de ponto
num diagrama, a previa ao vivo continua sendo a ferramenta.

## Livro escaneado de lado

PDF de livro digitalizado costuma trazer paginas com rotacao declarada (`/Rotate 90`,
`180`, `270`). O app trata isso: voce seleciona o diagrama **como ele aparece na tela**,
e a substituicao cai no lugar certo, com o tabuleiro novo de pe.

Isso valia so para paginas sem rotacao ate o Sprint 9.18. Antes, numa pagina girada, o
diagrama antigo sobrevivia no PDF exportado e o tabuleiro novo ia para outro canto,
deitado.

Rotacao combinada com uma `CropBox` deslocada da `MediaBox` — livro escaneado de lado
**e** preparado para impressao — ficou de fora ate o Sprint 9.19, quando o app passou a
recusar a exportacao em vez de grava-la errada. Desde o Sprint 9.20 essa combinacao
tambem e suportada: as quatro rotacoes, com qualquer `CropBox` e qualquer `MediaBox`.
Nao ha mais limitacao conhecida de geometria de pagina.

## Coordenadas do diagrama original

O diagrama do livro quase sempre traz as coordenadas impressas em volta do
tabuleiro (`a`-`h` embaixo, `1`-`8` na lateral). O whiteout cobre o tabuleiro e um
padding pequeno; as coordenadas ficam **fora** dele e sobrevivem a substituicao,
emoldurando o diagrama novo com as letrinhas do antigo.

Em `Ajustes` > `Aparência do diagrama`, **`Apagar coordenadas do diagrama original`**
resolve isso automaticamente, junto do whiteout — e aparece na previa ao vivo,
entao voce ve o efeito antes de exportar.

A deteccao e conservadora de proposito, porque apagar texto do livro por engano e
muito pior que deixar uma letrinha. Ela aceita duas formas: uma **fileira** de pelo
menos 4 coordenadas soltas alinhadas, ou uma **corrida contigua e em ordem** como
`abcdefgh` (que e como o PDF costuma guardar a linha inteira). E o que impede de
comer o `e` de "brancas jogam **e** ganham" numa legenda logo abaixo do diagrama —
e o que separa `cdef` de `faced`.

Medido em quatro livros reais (paginas 20-60):

| Livro | Diagramas com coordenadas apagadas |
|---|---|
| *A Matter of Endgame Technique* | 142 de 147 |
| *1001 Sacrificios* | 0 de 43 — o livro nao imprime coordenadas |
| *400 Quebra-cabecas* | 0 de 40 — idem |
| *Chess Structures* | 0 de 85 — pagina escaneada, sem texto |

**O limite:** num PDF escaneado as coordenadas sao pixels, e nenhuma leitura de
texto as encontra. Ali a ferramenta continua sendo o `Padding whiteout` por lado,
que se aplica em massa com `Aplicar em todas as substituicoes`.

A escolha e salva no projeto. Projetos antigos abrem com a opcao **desligada**: o
PDF que voce ja conferiu nao pode mudar sozinho.

## O painel lateral

O painel tem quatro abas, repartidas **por assunto**, e o criterio de qual coisa vai
para onde cabe numa frase: na aba do fluxo fica o que e etapa do fluxo.

| Aba | O que tem |
|---|---|
| `Diagrama` | as quatro etapas numeradas: `1 · Reconhecer`, `2 · Conferir a prévia`, `3 · Aplicar`, `4 · Alterações` |
| `Conferir` | a fila de candidatos de um lote — **so aparece quando ha fila**, com a contagem no titulo |
| `FEN` | a FEN, os avisos de legalidade e os metadados da posicao |
| `Ajustes` | `Aparência do diagrama` e `Reconhecimento`, os dois recolhidos por padrao |

Os grupos recolhíveis (`2 · Conferir a prévia`, `Aparência do diagrama`,
`Reconhecimento`) **lembram** se voce os deixou abertos ou fechados — nos dois
sentidos, e entre sessoes.

O editor de tabuleiro fica acima das abas, com a paleta de peças **ao lado** do
tabuleiro (nao acima) e os quatro comandos numa linha so.

Isso tudo importa por uma medicao. Numa janela de 1500x900 a aba do fluxo pedia 745 px
e recebia 222, entao `Adicionar substituicao` ficava abaixo da dobra e exigia rolar. Hoje
a mesma aba pede 643 px e recebe 352:

| | antes | hoje |
|---|---|---|
| falta para o passo final em 900 px | 191 px | **0 px** |
| altura minima, previa expandida | 1.100 px | **880 px** |
| altura minima, previa recolhida | 1.050 px | **790 px** |

**O fluxo basico cabe em 1500x900 sem rolar e com a previa expandida** — sem esconder
o resultado, sem encolher o tabuleiro e sem remover controle nenhum. O que mudou foi
onde as coisas moram.

Na hierarquia dos botoes: a **acao principal do momento** aparece preenchida de azul
(um botao por vez), as acoes secundarias ficam com o botao normal, e os comandos
**destrutivos** (`Remover`, `Limpar`, `Descartar`) sao achatados — sem preenchimento,
contorno discreto — recuperando contraste quando o cursor chega neles.

## Prévia ao vivo do resultado

Voce nao precisa exportar o PDF para saber como o diagrama vai ficar.

- **`Prévia do resultado` (Ctrl+D)**, na toolbar ou no menu `PDF`: a pagina passa a
  mostrar o resultado das alteracoes em vez do PDF original. Pressione de novo para voltar.
- **`Prévia (antes / depois)`**, na aba `Diagrama`: miniaturas lado a lado do diagrama que
  voce esta editando.

A prévia inclui a substituicao **antes de voce clicar em `Adicionar substituição`**:
basta selecionar a area e montar a posicao. Ela acompanha ao vivo:

- pecas movidas no editor de tabuleiro;
- edicao direta do campo FEN;
- `Padding whiteout` por lado e `Borda`;
- `Aplicar whiteout antes do overlay`, `Link Lichess por padrão` e a escolha de link
  do proprio diagrama;
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

### Comparar com cortina

A prévia cheia troca a pagina inteira de uma vez, e por isso ela responde "como vai
ficar" mas nao "o que mudou": os dois bitmaps nunca estao na tela juntos.

- **`Comparar com cortina` (Ctrl+Shift+D)**, na toolbar, no menu `PDF` ou na aba `Diagrama`:
  uma linha vertical divide a pagina. A **esquerda** fica o PDF original, a **direita** o
  resultado.
- **Arraste a linha** para varrer a pagina. Ela pode ser agarrada em qualquer altura, e a
  alca acompanha a parte da pagina que esta a vista.
- Levada até a borda, a cortina vira um limpa-vidros: a pagina inteira de um lado so.

Onde voce deixou a linha fica guardado — trocar de pagina ou fechar o app nao a devolve
para o meio.

A cortina e a prévia cheia sao **exclusivas entre si**: ligar uma desliga a outra, porque
as duas disputariam a mesma pagina. Enquanto a cortina esta ligada, o veu vermelho da
selecao sai (ele cairia sobre os dois lados e tingiria justamente o que se quer
comparar); o contorno e as alcas ficam, entao a selecao ainda pode ser ajustada. O titulo
da janela mostra `[comparação: antes | depois]`.

Nao ha custo novo de render: os dois bitmaps sao os mesmos que a prévia ja produzia.

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
`Salvar agora`.

A gravacao e atomica e **duravel**: o projeto vai primeiro para um arquivo
temporario, os bytes sao forcados para o disco (`fsync`) e so entao ele toma o lugar
do anterior. Um autosave interrompido no meio — disco cheio, `Ctrl+C`, queda de
energia — nao corrompe o projeto anterior **nem deixa arquivo temporario para tras**.

## Comparar dois processamentos

Reprocessou o livro com um motor melhor e quer saber o que mudou?
`Arquivo` > `Comparar projetos...` — ou o script:

```powershell
python scripts/project_diff.py --before .\antigo.json --after .\novo.json
```

O diff nao casa os diagramas por retangulo exato: um detector melhor devolve a mesma
moldura alguns pontos diferente, e por chave exata **todo** diagrama reenquadrado
apareceria como removido e readicionado. O casamento e por sobreposicao (mesma pagina,
IoU >= 0,50), e cada par casado diz **em que** difere:

| Motivo | Significa |
|---|---|
| `fen` | o motor leu a posicao de outra forma — e o que vale conferir |
| `retangulo` | mesmo diagrama, moldura reenquadrada |
| `confianca` | mesma leitura, outra certeza |
| `estilo` | padding ou borda |
| `lado_ou_lance` | lado a jogar ou numero do lance |

Num reprocessamento a maioria vai ser `retangulo+confianca` (ruido esperado); a linha
`das alteradas, N mudaram de FEN` e a lista de revisao de verdade.

Se os dois projetos apontarem para **PDFs diferentes** (sha256 distinto), o diff avisa
antes de mostrar numero nenhum — comparar projetos de livros diferentes nao quer dizer
nada.

`--json diff.json` grava o diff completo para outra ferramenta. Codigos de saida:
`0` igual, `1` houve diferenca, `2` livros diferentes.

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

```powershell
python scripts/project_diff.py --before .\antigo.json --after .\novo.json --json .\diff.json
```

## Estrutura principal

```text
src/chess_pdf_editor/
  app.py              # janela principal: composicao, modos, previa ao vivo
  ocr_workflow.py     # mixin: reconhecimento, lote e fila de candidatos
  study_workflow.py   # mixin: posicoes de estudo do PDF e comentarios por lance
  study_panel.py      # painel de estudo (nao conhece a janela)
  gallery.py          # galeria antes/depois do livro, com worker proprio
  navigator.py        # um diagrama por vez, grande, com as etiquetas ao lado
  theme.py            # cores semanticas e QSS reutilizado
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
  recognition_snapshot.py # um JSON por reconhecimento, ao lado do PDF
  logging_config.py   # log em arquivo com rotacao
  fen.py              # utilitarios e validacoes FEN
  legality.py         # auditoria de legalidade da posicao (impossivel/suspeita)
  renderer.py         # render do diagrama (PDF/PNG/SVG)
  diagram_export.py   # um arquivo por diagrama + indice.csv
  style_batch.py      # experimentar estilo no livro antes de aplicar
  study.py            # arvore de lances/variantes do modo Estudo
  project_state.py    # persistencia de checkpoint
  migrations.py       # migracoes explicitas do `schema_version` do projeto
  project_diff.py     # o que mudou entre dois projetos salvos
  types.py            # dataclasses do contrato: operacao, apagamento, leitura do OCR
  resources.py        # onde achar `models/` e `assets/`, do repo ou do executavel
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
`--skip-smoke`, `--light`, `--installer`.

#### Instalador

```powershell
python scripts/build_exe.py --installer
```

Compila `packaging/installer.iss` com o **Inno Setup 6** e sai em
`dist/ChessPdfEditor-<versao>-<variante>-setup.exe`. Roda depois do `--self-test`,
para nao gastar minutos comprimindo um bundle que o proprio build vai reprovar.

Precisa do Inno Setup instalado — o script o procura no `PATH` e nos caminhos
padrao, ou na variavel `INNO_SETUP_ISCC`. Sem ele, `--installer` **falha** dizendo
onde baixar: quem pediu um instalador e recebeu um build verde sem instalador foi
enganado pelo proprio build. O resto da entrega nao depende disso — a pasta em
`dist/` continua valendo.

A instalacao e **por usuario** por padrao, sem exigir administrador; quem quiser
para a maquina toda escolhe no dialogo. Sem assinatura de codigo o SmartScreen
avisa de todo modo, e exigir UAC por cima disso so somaria obstaculo.

Instalar a variante leve **por cima** da completa troca de variante de verdade: o
instalador limpa o `_internal` anterior antes de copiar. Sem isso o torch da
instalacao antiga ficaria para tras, e o pacote se diria `light` com o motor local
ainda ao lado.

#### Build leve (sem reconhecimento local)

```powershell
python scripts/build_exe.py --light
```

Mesmo app, **sem** o motor local — nem torch, nem OpenCV, nem o classificador. Sai em
`dist/ChessPdfEditor-lite/`, e cada variante fica na sua pasta.

| Variante | Tamanho | Build |
|---|---|---|
| completa | 719 MB | varios minutos |
| light | **193 MB** | **1,1 min** |

O reconhecimento nessa variante usa o **servico externo**, e ela ja abre com o motor
`Somente remoto` selecionado — o padrao hibrido precisaria do motor local que ela nao
tem. A mensagem de indisponibilidade tambem muda: em vez de mandar rodar `pip install`
(impossivel para quem so tem o `.exe`), ela explica que a versao foi distribuida sem o
reconhecimento local e aponta o servico externo.

O `--self-test` roda nas duas, checando o contrato de cada uma: na completa, que o
classificador **esta** no bundle e carrega; na light, que ele e o torch **nao** estao —
um "download menor" que saiu com 500 MB de torch dentro e um build falho.

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
