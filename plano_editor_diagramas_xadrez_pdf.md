# Plano de Implementação (Skill): Editor de Diagramas de Xadrez em PDF

**Versão:** 1.3 (backlog de melhorias)  
**Status:** Planejamento / Especificação técnica (MVP → Beta)  
**Stack alvo:** Python 3.9+ (recomendado 3.11+)  
**Última revisão:** 2026-05-17  

---

## 1) Objetivo do Produto

Construir uma aplicação desktop que:

1. **Encontra diagramas de xadrez** em páginas de PDFs (escaneados e/ou digitais).
2. **Reconhece a posição** (peças por casa) e gera **FEN** confiável.
3. **Substitui o diagrama original** (baixa qualidade) por um **diagrama em alta qualidade**, preferencialmente **vetorial**, preservando:
   - tamanho (largura/altura),
   - posição (coordenadas exatas na página),
   - e layout do documento (sem deslocar texto/elementos).

### Resultados esperados
- PDF final com diagramas nítidos (vetor ou raster HQ), alinhados exatamente onde estavam.
- Fluxo de correção eficiente: o usuário consegue validar/corrigir em poucos cliques.
- Pipeline reutilizável (CLI) + GUI (produtividade).

---

## 2) Escopo, Não‑Escopo e Requisitos

### 2.1 Escopo (MVP)
- Abrir PDF, navegar páginas, renderizar preview.
- Detecção automática de candidatos a diagrama (com fallback manual).
- Extração do tabuleiro (crop + correção de perspectiva).
- Reconhecimento das 64 casas (classe vazia + 12 peças).
- Montagem do **piece placement** do FEN (ex.: `rnbqkbnr/pppppppp/8/...`).
- Editor visual para correção (clique nas casas + paleta de peças).
- Inserção no PDF:
  - **Preferência**: inserção de **vetor** (via “show page” de um PDF gerado do SVG).
  - **Fallback**: PNG HQ (300–600 DPI efetivos) com antialias.

### 2.2 Não‑Escopo (por enquanto)
- Reconhecer comentários, setas, marcações manuais (círculos, arrows), coordenadas (a‑h/1‑8) ou texto em volta.
- Reconhecer diagramas **parciais** (menos de 8×8) no MVP (pode entrar em v2).
- “Reflow” de PDFs (o produto é **edição visual**, não re-diagramação do livro).

### 2.3 Requisitos não‑funcionais (meta)
- **Precisão**: minimizar trocas graves (rei/rainha, cor errada).
- **Rapidez**: processar uma página típica em < 1s (CPU) após cache.
- **Confiabilidade**: salvar trabalho incremental (projeto com estado).
- **Reprodutibilidade**: pipeline determinístico para batch/CLI.
- **Auditabilidade**: logs + export de relatório (página, bbox, FEN, confiança).
- **Portabilidade**: instalação reproduzível em Windows (prioritário) e Linux.

---

## 3) Stack Tecnológico (atualizado)

| Componente | Tecnologia | Observações práticas |
|---|---|---|
| Linguagem | Python 3.9+ | Recomenda-se 3.11+ para performance/typing |
| GUI | **PySide6** (ou PyQt6) | PySide6 costuma ser mais simples em licenças; ambos ok |
| Render PDF | PyMuPDF (fitz) | Render rápido + edição sólida |
| CV | OpenCV (cv2) | Pré-processamento + detecção + perspectiva |
| ML | PyTorch | Classificação 13 classes |
| Xadrez | python-chess | Validação + geração de SVG |
| Vetor (opcional) | **CairoSVG** | Converter SVG → PDF (mantém vetor) |
| Raster HQ | Pillow | Redimensionamento/antialias e export |

> Nota: manter o core sem depender de CairoSVG é útil (fallback raster).

---

## 4) Arquitetura (módulos e contratos)

### 4.1 Visão macro (pipeline)
```mermaid
flowchart LR
  A[PDF: página] --> B[Render preview (pixmap)]
  B --> C{Auto-detect tabuleiros}
  C -->|OK| D[Extrair ROI + corrigir perspectiva]
  C -->|Falha| E[Seleção manual ROI]
  D --> F[Grid 8x8 + normalização]
  E --> F
  F --> G[Inferência ML por casa]
  G --> H[Montagem FEN + score/confiança]
  H --> I[Editor visual (correções)]
  I --> J[Render HQ (SVG->PDF ou PNG HQ)]
  J --> K[Inserir/overlay no PDF]
  K --> L[Salvar novo PDF + relatório]
```

### 4.2 Separação em camadas (recomendação)
- **Core (sem GUI)**: `cv`, `ml`, `chess`, `pdf`, `pipeline`
- **GUI**: apenas orquestra e chama o core (facilita testes)
- **CLI/Batch**: reusa o mesmo core

### 4.3 Contratos (dados)
Padronize estruturas simples para “passar dados” entre módulos.

**Tipos recomendados:**
- `DiagramCandidate`: `page_num`, `bbox_pdf` (Rect), `bbox_img` (x,y,w,h), `score_detect`
- `BoardCrop`: imagem corrigida 8×8, metadados de orientação e escala
- `InferenceResult`: matriz 8×8 de classes + confidências
- `FenResult`: FEN + validações + score
- `ReplaceOp`: `page_num`, `bbox_pdf`, `asset_bytes`, `asset_type` (pdf|png), `dpi`, `method`

---

## 5) Detecção de Diagramas (OpenCV) — robustez real

### 5.1 Estratégia híbrida (melhor que “apenas contornos”)
1. **Pré-processamento adaptativo**:
   - grayscale
   - `cv2.GaussianBlur` leve
   - binarização adaptativa (ou Otsu + morfologia)
2. **Heurística de “grid 8×8”**:
   - Hough Lines (linhas horizontais/verticais)
   - ou detecção de padrões repetitivos (picos em projeções)
3. **Validação geométrica**:
   - quadrilátero convexo
   - proporção ~ 1:1
   - presença de linhas internas (mínimo de 7 divisões em cada direção)
4. **Score final** combinando:
   - “gridness” + área + retidão + contraste

> Isso reduz falsos positivos (ex.: tabelas, gráficos, quadros decorativos).

### 5.2 Correção de perspectiva e normalização
- Encontrar 4 cantos (linhas → interseções ou contorno principal).
- `cv2.getPerspectiveTransform` + warp para um **quadrado canônico** (ex.: 512×512).
- Definir grid 8×8 com coordenadas **inteiras** (casas exatas).

### 5.3 Orientação do tabuleiro (importante)
Problema: alguns diagramas podem estar invertidos (brancas em cima).

Abordagens:
- **Heurística por material**: peões tendem a estar em ranks 2/7 (em posições “normais”).
- **Heurística por “legalidade fraca”**: ambos os reis devem existir e não podem ocupar mesma casa.
- **Auto-rotacionar**: testar 4 rotações; escolher a que maximiza a confiança global da inferência + validade.

---

## 6) Reconhecimento de Peças (ML) — plano mais operacional

### 6.1 Classes
- 0: vazio
- 1–6: P, N, B, R, Q, K (brancas)
- 7–12: p, n, b, r, q, k (pretas)

### 6.2 Dataset sintético (domínio‑randomizado)
Além do seu plano original, incluir:
- **Variação de temas**: contraste baixo, papel amarelado, sombras.
- **Variação de impressão**: halftone, bleed, artefatos de scanner.
- **Bordas/ruído de recorte**: uma casa “invade” a outra (padding aleatório).
- **Linhas grossas/falhas**: bordas apagadas, linhas duplas.
- **Compressão**: JPEG com qualidade variável + downscale/upsample.

Sugestão de geração:
- Renderizar o board inteiro → depois cortar as 64 casas.
- Guardar metadados: fonte/tema/seed/transform (para rastrear erros).

### 6.3 Modelo e calibração
- Arquitetura: **ResNet18** ou **MobileNetV3-Small** adaptada para 1 canal.
- Treino:
  - `CrossEntropyLoss`
  - `WeightedRandomSampler` ou pesos por classe (vazio domina)
  - Augmentations (torchvision transforms) consistentes com seu CV
- Métricas:
  - acurácia por classe
  - matriz de confusão
  - “board accuracy”: % de tabuleiros com **64/64** corretas (métrica dura)
  - “edit distance” (número médio de casas erradas por diagrama)

### 6.4 Rejeição (não forçar resposta)
Introduzir um estado **UNKNOWN** via regra:
- se `max_prob < threshold` numa casa, marcar como “incerta”
- GUI exige revisão das casas incertas (fluxo assistido)

Isso reduz erros silenciosos.

### 6.5 Fine‑tuning com feedback do usuário
- Quando o usuário corrige:
  - salvar `crop_board.png`, `grid_cells/*.png`, `fen_final.txt`, `rotation`, `bbox`
- Periodicamente:
  - re-treinar (ou fine-tune) com esses exemplos reais

---

## 7) Montagem e Validação do FEN (python-chess)

### 7.1 FEN mínimo vs completo
Para diagramas, o essencial é o **piece placement**.  
Você pode gerar:
- **FEN mínimo**: `piece_placement w - - 0 1` (default)
- **FEN estendido (opcional)**: permitir usuário definir lado a jogar

### 7.2 Validações úteis (sem virar “motor”)
- Exatamente 1 rei branco e 1 rei preto.
- Nenhuma casa com duas peças.
- Peões não podem estar na 1ª/8ª fileira (regra prática; exceções raras podem ser tratadas como aviso).
- Se falhar, destacar como **warning** e pedir revisão.

---

## 8) Renderização HQ (Vetor preferencial + fallback)

### 8.1 Vetor (recomendado)
Pipeline:
1. `python-chess` gera SVG do tabuleiro.
2. Converter SVG → **PDF vetorial** (CairoSVG).
3. Inserir no PDF alvo via PyMuPDF usando “show page” (colocar a página do PDF gerado dentro do retângulo do diagrama).

**Vantagens:**
- zoom infinito (texto/linhas nítidos)
- arquivo final menor que PNG gigante (muitas vezes)

### 8.2 Raster fallback (alta qualidade)
- Gerar SVG → raster (PNG) com dimensão suficiente para a bbox no PDF:
  - objetivo: 300–600 DPI efetivos no retângulo final
- Inserir com `page.insert_image(rect, stream=...)`

---

## 9) Inserção no PDF (PyMuPDF) — detalhes que evitam bugs

### 9.1 Conversão de coordenadas (pixel ↔ PDF)
- Render do preview usa uma `Matrix(scale_x, scale_y)`.
- Armazene sempre:
  - `matrix` usada
  - bbox no espaço de **imagem**
  - bbox no espaço de **PDF points**
- Quando usuário seleciona no preview:
  - converter para PDF via inversa da matrix

Cuidados obrigatórios para não deslocar overlay:
- considerar `page.rotation` (0/90/180/270) na ida e volta de coordenadas.
- trabalhar com `page.rect`/`cropbox` de forma explícita (não assumir origem simples).
- guardar um `TransformContext` por página: `rotation`, `cropbox`, `matrix_preview`, `zoom`, `dpi`.
- padronizar uma função única de conversão (evitar lógica duplicada GUI/core).

Contrato sugerido:
```python
@dataclass
class TransformContext:
    page_num: int
    rotation: int
    cropbox: tuple[float, float, float, float]
    matrix_preview: tuple[float, float, float, float, float, float]

def img_rect_to_pdf_rect(img_rect, ctx: TransformContext) -> tuple[float, float, float, float]:
    """Converte bbox do preview para coordenadas PDF points, respeitando rotação/crop."""
    ...
```

Teste mínimo obrigatório:
- para cada página de teste (incluindo rotações 90/180/270), validar round-trip:
  - `pdf -> img -> pdf` com erro absoluto <= 1.0 point por borda.

### 9.2 Estratégia “não destrutiva”
- Não remover a imagem antiga (pode ser difícil dependendo do PDF).
- Fazer **overlay** com o diagrama novo por cima.
- Opcional (v2): “whiteout” do fundo com um retângulo branco antes (se o diagrama original sangra bordas).

Ajuste recomendado para produção:
- tornar **whiteout padrão configurável** (`on` por default no MVP).
- ordem de desenho:
  1) retângulo branco no `bbox_pdf` (com pequena margem opcional, ex.: +0.5 pt)
  2) overlay do diagrama vetorial/raster
- oferecer opção de export:
  - `overlay_only` (sem whiteout)
  - `whiteout_overlay` (padrão)
- incluir preview “antes/depois” com e sem whiteout para inspeção rápida.

### 9.3 PDFs protegidos
- Detectar criptografia/senha.
- Se não puder editar, exportar como **novo PDF** (mesmo assim pode falhar se for “no copy/edit” estrito).

---

## 10) GUI (PySide6) — UX para produtividade

### 10.1 Layout sugerido (MVP)
- Esquerda: viewer do PDF (zoom/pan, página atual)
- Direita (tabs):
  - **Detecção**: lista de candidatos (página, miniatura, score)
  - **Edição**: tabuleiro interativo + FEN + warnings
  - **Exportação**: opções de saída + relatório

### 10.2 Editor de tabuleiro (muito melhor que “só campo FEN”)
- Grid 8×8 clicável
- Paleta de peças (brancas/pretas/vazio)
- Atalhos:
  - clique direito para limpar
  - scroll para alternar peça
  - Ctrl+Z / Ctrl+Y (undo/redo)
- Campo FEN sincronizado (edição textual também funciona)

### 10.3 Preview antes de gravar
- “Antes / Depois”:
  - overlay do diagrama HQ no preview da página
  - toggle para comparar

### 10.4 Estado do projeto
- Salvar automaticamente um `.json` (checkpoint):
  - diagramas detectados, FENs confirmados, bboxes
- Se o app fechar, continua de onde parou.

Reforço de robustez:
- incluir `schema_version` e `app_version` no topo do arquivo.
- incluir `source_pdf_fingerprint` (hash + tamanho + mtime) para detectar troca de arquivo.
- incluir migração de schema (`v1 -> v2`) com fallback seguro e log.

---

## 11) Estrutura de Diretórios (recomendada)

```
chess_pdf_editor/
├── README.md
├── pyproject.toml              # (ou requirements.txt)
├── src/
│   └── chess_pdf_editor/
│       ├── app/                # GUI
│       ├── pipeline/           # orquestração do core
│       ├── pdf/                # PyMuPDF wrapper
│       ├── cv/                 # detecção/crops/perspectiva
│       ├── ml/                 # modelo, dataloaders, inferência
│       ├── chess/              # FEN, SVG render, validações
│       ├── storage/            # cache + projetos + relatórios
│       └── utils/              # logging, types, configs
├── assets/
│   ├── fonts/
│   └── icons/
├── data/
│   ├── synthetic/
│   ├── real_samples/
│   ├── feedback/
│   └── models/
├── scripts/
│   ├── gen_dataset.py
│   ├── train.py
│   └── batch_replace.py
└── tests/
    ├── test_fen_validation.py
    ├── test_grid_detection.py
    └── test_pdf_overlay.py
```

---

## 12) Snippets (mais “concretos” e prontos para evoluir)

### 12.1 Interface do pipeline (core)
```python
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class DiagramCandidate:
    page_num: int
    bbox_pdf: Tuple[float, float, float, float]   # x0,y0,x1,y1 (points)
    score: float

@dataclass
class FenResult:
    fen: str
    confidence: float
    warnings: List[str]

def process_candidate(pdf_path: str, candidate: DiagramCandidate) -> FenResult:
    ...
```

### 12.2 Inserção vetorial via “show page” (conceito)
- Gerar um PDF de 1 página contendo o diagrama (a partir do SVG).
- Inserir essa página dentro do `rect` na página alvo.

```python
import fitz  # PyMuPDF

def overlay_pdf_page(target_doc: fitz.Document, page_num: int, rect, diagram_pdf_bytes: bytes):
    src = fitz.open("pdf", diagram_pdf_bytes)
    page = target_doc[page_num]
    page.show_pdf_page(fitz.Rect(rect), src, 0)  # coloca vetor dentro do retângulo
```

### 12.3 Inserção raster (fallback)
```python
import fitz

def overlay_png(target_doc: fitz.Document, page_num: int, rect, png_bytes: bytes):
    page = target_doc[page_num]
    page.insert_image(fitz.Rect(rect), stream=png_bytes, overlay=True)
```

---

## 13) Testes, QA e Métricas

### 13.1 Conjunto “golden”
- Separar 30–100 diagramas reais representativos (vários livros/estilos).
- Para cada diagrama:
  - bbox correta
  - FEN correto
  - snapshot do “antes/depois” para inspeção visual

### 13.2 Métricas operacionais
- % de diagramas detectados automaticamente (sem intervenção)
- % de FENs corretos sem correção
- # médio de casas corrigidas por diagrama
- tempo médio por diagrama (com/sem correção)

### 13.3 Gates de aceite (MVP/Beta)
Definir critérios objetivos para evitar “pronto subjetivo”.

MVP (mínimo):
- detecção automática >= 70% em conjunto golden.
- FEN correto sem edição >= 85% (com fallback manual sempre disponível).
- tempo médio por diagrama (com revisão) <= 20s em CPU.
- erro de alinhamento visual <= 1.5 pt no PDF final.

Beta (meta):
- detecção automática >= 85% em conjunto golden.
- FEN correto sem edição >= 93%.
- # médio de casas corrigidas <= 1.0 por diagrama.
- tempo médio por diagrama (com revisão) <= 10s.

---

## 14) Riscos e Mitigações (atualizado)

| Risco | Impacto | Mitigação prática |
|---|---|---|
| Falsos positivos (tabelas/quadros) | Troca indevida no PDF | Score + confirmação visual + lista de candidatos |
| Baixa resolução extrema | Confusão peça/vazio | Threshold + casas “incertas” forçando revisão |
| Estilos muito diferentes | queda de precisão | domain randomization + feedback + fine-tuning |
| Diagramas inclinados | grid errado | perspectiva robusta + validação por “gridness” |
| PDF protegido | sem edição | exportar novo PDF / alertar usuário |
| Desalinhamento (bbox) | overlay fora do lugar | conversão coordenadas rigorosa + preview antes de salvar |

---

## 15) Roadmap (realista e incremental)

### Sprint 1 (core mínimo + baseline de QA)
- Render PDF + seleção manual de ROI
- Warp perspectiva + grid 8×8
- Inserção raster (PNG HQ) no PDF
- Criar conjunto golden inicial (mín. 30 casos) + script de avaliação

### Sprint 2 (ML inicial)
- Dataset sintético + treino modelo 13 classes
- Inferência por casa + FEN
- Editor visual do tabuleiro (correção)
- Implementar checkpoint versionado (`schema_version`) + autosave

### Sprint 3 (detecção automática)
- Detector de candidatos + score
- Rotação/auto-orientação por validação
- Testes de round-trip de coordenadas (incluindo páginas rotacionadas)

### Sprint 4 (vetor + polimento)
- SVG → PDF (CairoSVG) + show_pdf_page
- Whiteout configurável + preview antes/depois
- Relatório de alterações (CSV/JSON)

### Sprint 5 (beta)
- Batch mode (processar vários diagramas)
- Cache de inferência + acelerações
- Hardening final: regressão completa no golden + gates MVP/Beta

---

## 16) Requisitos de Instalação (sugestão)

### requirements.txt (mínimo)
```txt
PySide6>=6.6.0
PyMuPDF>=1.23.0
opencv-python>=4.8.0
numpy>=1.24.0
pillow>=10.0.0
python-chess>=1.999
torch>=2.0.0
torchvision>=0.15.0
```

### extras (vetor)
```txt
cairosvg>=2.7.0
```

### Notas de instalação reproduzível (Windows/Linux)
- Priorizar Python 3.11 x64.
- Fixar versões em ambiente de release (`requirements-lock.txt`).
- `torch/torchvision`: instalar pares compatíveis (CPU ou CUDA) no mesmo índice.
- `cairosvg` no Windows pode exigir runtime nativo; manter fallback raster ativo por padrão.
- Validar setup com script `scripts/check_env.py` (import + versão + smoke test).

---

## 17) Entregáveis recomendados (para “fechar” o projeto)
- App GUI (Windows/Linux) com instalador/zip.
- CLI para batch (`batch_replace.py`) e logs.
- Pasta `golden/` com casos reais e um script de avaliação.
- Modelo `.pth` versionado + `model_card.md` (dados, métricas, limitações).
- `project_state.json` para retomar trabalho.

---

## 18) Checklist de aceitação (MVP)
- [ ] Selecionar diagrama manualmente e gerar FEN
- [ ] Corrigir FEN no editor visual
- [ ] Gerar diagrama HQ e inserir no PDF no mesmo lugar
- [ ] Salvar PDF de saída sem quebrar layout
- [ ] Reabrir projeto e continuar (checkpoint)
- [ ] Rodar em pelo menos 3 PDFs reais diferentes

---

## 19) Backlog de melhorias futuras

Esta seção registra melhorias identificadas na revisão do projeto em 2026-05-17. Elas não fazem parte de uma implementação imediata, mas servem como guia para as próximas etapas.

### 19.1 Prioridade alta

- [ ] **Mover OCR e exportação para workers em segundo plano**
  - Hoje o reconhecimento por OCR e a exportação do PDF rodam na thread principal da interface.
  - Para PDFs grandes ou endpoints lentos, a janela pode parecer travada.
  - Implementar com `QThread`, `QRunnable`/`QThreadPool` ou uma camada equivalente de worker.
  - Manter progresso, cancelamento e propagação clara de erros para a UI.

- [ ] **Dividir `app.py` em módulos menores**
  - O arquivo principal concentra UI, OCR, estado do projeto, estudo, overlays e exportação.
  - Separação sugerida:
    - `main_window.py`: janela principal e composição de telas.
    - `study_panel.py`: painel/modo de estudo.
    - `ocr_workflow.py`: reconhecimento de seleção, página atual e lote.
    - `operations.py`: criação, atualização e validação de operações.
    - `settings.py`: preferências persistidas via `QSettings`.
  - Objetivo: reduzir acoplamento, facilitar testes e tornar futuras mudanças menos arriscadas.

- [ ] **Adicionar GitHub Actions para testes**
  - Rodar `pytest` automaticamente em push e pull request.
  - Começar com matriz simples em Windows e Python estável.
  - Validar pelo menos testes unitários sem dependência de interface gráfica real.

### 19.2 Prioridade média

- [ ] **Melhorar configuração do OCR**
  - Evitar endpoint duplicado/hardcoded em múltiplos lugares.
  - Centralizar endpoint padrão, fallback e timeout.
  - Persistir endpoint escolhido pelo usuário em `QSettings`.
  - Permitir configuração por variável de ambiente para uso em scripts e ambientes automatizados.

- [ ] **Adicionar logs estruturados**
  - Registrar falhas de OCR, renderização, exportação e carregamento de projetos.
  - Evitar `except Exception` silencioso em pontos críticos.
  - Usar `logging` com arquivo local opcional, por exemplo `logs/chess_pdf_editor.log`.
  - Exibir mensagens amigáveis na UI, mantendo detalhes técnicos no log.

- [ ] **Criar migrações explícitas para `project_state.json`**
  - O projeto já tem `schema_version`.
  - Adicionar funções de migração entre versões para preservar compatibilidade com projetos antigos.
  - Testar carregamento de arquivos de estado salvos por versões anteriores.

- [ ] **Ampliar testes de integração**
  - Testar aplicação de overlay em PDF real de amostra.
  - Testar inserção de link Lichess.
  - Testar salvar/carregar projeto com operações, apagamentos e posições de estudo.
  - Testar OCR com mock HTTP, sem depender da internet.
  - Testar renderização com Merida, sem Merida e com fallback raster.

### 19.3 Prioridade baixa / empacotamento

- [ ] **Gerar executável Windows**
  - Avaliar PyInstaller ou Nuitka.
  - Incluir assets necessários, como fonte Merida e sons.
  - Criar script de build reproduzível.
  - Validar execução em máquina limpa sem ambiente de desenvolvimento.

- [ ] **Melhorar relatório de processamento**
  - Exportar CSV/JSON com página, bbox, FEN, origem da operação, confiança e avisos.
  - Útil para auditoria e comparação entre versões do OCR.

- [ ] **Documentar fluxo de desenvolvimento**
  - Adicionar seção no README com:
    - instalação para desenvolvimento;
    - execução de testes;
    - execução do app;
    - padrão para commits/releases.

### 19.4 Ordem sugerida de implementação

1. Adicionar GitHub Actions com `pytest`.
2. Criar workers para OCR/exportação sem travar a interface.
3. Separar `app.py` em módulos menores.
4. Centralizar configuração do OCR.
5. Adicionar logs estruturados.
6. Implementar migrações de projeto.
7. Expandir testes de integração.
8. Preparar empacotamento Windows.
