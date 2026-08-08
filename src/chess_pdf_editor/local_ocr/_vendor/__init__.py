"""Cópia fiel do caminho de inferência do **ChessVisionOFF_Puro**.

Origem: `C:\\PythonChess\\ChessVisionOFF_Puro`, pacote `chess_diagram_ocr`,
commit `ee308dd`, importado em 2026-08-08.

Módulos trazidos (só o necessário para reconhecer, nada de treino/dataset/UI):

    config.py           constantes e limiares medidos
    model.py            arquitetura + pré-processamento da casa
    checkpoint.py       leitura do .pt com metadados
    board_detection.py  detector por contorno + warp + fatiamento em 64 casas
    fen_utils.py        legalidade, FEN a partir das classes, prior de peões
    decode.py           decodificação sujeita às regras do xadrez
    inference.py        carga do modelo, predição e escolha de orientação

**Não edite nada aqui.** Correção de bug ou melhoria vai no projeto de origem e volta
como recópia — assim `diff` contra o upstream continua sendo a forma de saber o que
mudou. A única divergência deliberada está no fim de `config.py`, marcada como tal:
os caminhos padrão de dataset/PDF apontavam para pastas que não existem neste projeto.

Por que copiar em vez de depender do pacote: o editor precisa rodar numa máquina que
não tem o ChessVisionOFF_Puro instalado — inclusive no executável Windows do Sprint 8.

O que este pacote **não** traz é o treino. Retreinar continua sendo trabalho do projeto
de origem, que tem o dataset rotulado (3.290 tabuleiros), os splits e as métricas.
`chess_pdf_editor.feedback` exporta as correções feitas aqui no formato que o
`data/labels.csv` de lá espera.
"""
