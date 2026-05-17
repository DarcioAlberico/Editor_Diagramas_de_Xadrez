# Chess PDF Editor

Aplicacao desktop para:
- abrir PDF;
- selecionar diagramas de xadrez;
- reconhecer posicao por OCR (API);
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

Opcional para usar fonte Merida no diagrama exportado:

1. Crie a pasta `assets/fonts/`
2. Coloque o arquivo `Merida.ttf` ou `Merida.otf` nela

Alternativa: defina variavel de ambiente `CHESS_MERIDA_FONT` apontando para `.ttf`/`.otf`.

Com fonte Merida configurada, o exportador gera overlay vetorial (PDF) com a fonte embutida no diagrama.
Sem Merida, `cairosvg` habilita fallback vetorial usando `python-chess`.

Opcional para usar imagens de pecas no editor/tabuleiro de estudo:
- O app detecta automaticamente `Python-Easy-Chess-GUI-master/Images/60`.
- Alternativa: defina `CHESS_PIECE_IMAGE_DIR` apontando para uma pasta com `wP.png`, `wK.png`, `bP.png`, etc.

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
   O modo em lote descarta deteccoes que ocupam mais de 50% da pagina (heuristica anti-falso-positivo).
   Se cancelar no meio, o proximo clique retoma da pagina pendente.
7. Corrija a posicao no `Editor de Tabuleiro` se necessario: selecione uma peça na paleta e clique na casa; clique direito limpa a casa.
8. Se necessário, abra `Aparência` > `Ajustes avançados` para ajustar `Padding whiteout` por lado e `Borda`.
   A opcao `Aplicar em todas as substituicoes` (ligada por padrao) replica a configuracao para toda a lista.
9. Clique em `Adicionar substituição`.
10. Para apagar coordenadas/letras residuais, selecione a area e clique em `Adicionar apagamento`.
    Substituições e apagamentos aparecem juntos na lista `Alterações`.
11. Repita para outros diagramas.
12. Clique em `Exportar PDF`.
   Opcional: na aba `Aparência`, abra `Ajustes avançados` e marque/desmarque `Incluir link Lichess no PDF exportado`.
   Quando habilitado, o PDF exportado inclui um link `Lichess` em azul abaixo de cada diagrama substituido.

## Modo Estudo (offline)

No toolbar, use `Estudo` para abrir um painel lateral com:
- movimentos legais (click na peca e depois na casa destino);
- seta do ultimo lance;
- lista SAN lateral (clique para navegar pelos lances);
- `Desfazer` / `Refazer`;
- `Resetar Linha`;
- `Importar PGN`, `Copiar FEN`, `Copiar PGN` e `Salvar PGN`.

Para estudar uma posicao do PDF:
1. Selecione o diagrama na pagina.
2. Reconheca ou monte/corrija a FEN no editor.
3. Clique em `Estudar selecao`.
4. Use `Atualizar linha` para salvar o PGN estudado naquela posicao.

As posicoes de estudo ficam em `Posicoes deste PDF` e sao salvas no projeto.

## Projeto (checkpoint)

- `Salvar Projeto`: salva operacoes pendentes em JSON.
- `Carregar Projeto`: restaura operacoes e pagina atual.

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
  app.py              # GUI principal
  widgets.py          # viewer selecionavel + editor de tabuleiro
  pdf_service.py      # render/crop/overlay no PDF
  ocr_api.py          # cliente da API OCR
  fen.py              # utilitarios e validacoes FEN
  renderer.py         # render do diagrama (PDF/PNG)
  project_state.py    # persistencia de checkpoint
```

## Observacoes

- O OCR e usado para acelerar reconhecimento, mas o fluxo principal de substituicao e local.
- O fallback de renderizacao funciona sem `cairosvg`.
- `Padding whiteout` por lado (esq/topo/dir/base) e `Borda` sao salvos por substituicao no projeto.
- `Apagamentos` sao salvos separadamente e aplicados antes dos overlays.
- Plano tecnico detalhado: `plano_editor_diagramas_xadrez_pdf.md`.
