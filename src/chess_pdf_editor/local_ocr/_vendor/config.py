from pathlib import Path
from typing import Literal

PIECE_CLASSES = [
    "empty",
    "P",
    "N",
    "B",
    "R",
    "Q",
    "K",
    "p",
    "n",
    "b",
    "r",
    "q",
    "k",
]

PIECE_TO_IDX = {name: idx for idx, name in enumerate(PIECE_CLASSES)}
IDX_TO_CLASS = {idx: name for name, idx in PIECE_TO_IDX.items()}

BOARD_SIZE = 800
CELL_SIZE = BOARD_SIZE // 8
MODEL_IMAGE_SIZE = 64

# Abaixo disso a casa entra em `BoardPrediction.uncertain_squares` (S-10). O valor e alto
# de proposito: o modelo e confiante ate quando erra (media ~0,80 nas casas erradas contra
# ~0,999 nas certas), entao um limiar baixo nao separaria nada.
UNCERTAIN_SQUARE_THRESHOLD = 0.90

# Decodificacao sujeita as regras do xadrez (S-11) em vez de argmax por casa.
# O efeito medido esta em docs/BASELINE.md; ligado por padrao porque o argmax nao tem
# nenhuma obrigacao de produzir posicao legal.
CONSTRAINED_DECODING = True

# Teto de casas que o reparo pode reescrever. Alto demais e o decodificador "conserta"
# uma leitura ruim inventando uma posicao legal e errada -- pior que admitir a falha.
MAX_DECODE_CHANGES = 6

# Gate de exportacao (S-15): abaixo desta confianca minima por casa a posicao vai para o
# arquivo de revisao em vez do PGN principal. Medido no split de teste: a confianca minima
# fica >= 0,90 em quase todo tabuleiro exato, e a media nas casas erradas e ~0,75 -- entao
# 0,80 pega a maior parte do erro sem mandar tabuleiro bom para revisao. Provisorio ate a
# calibracao da S-28 dar um numero derivado da curva.
ACCEPT_MIN_CONFIDENCE = 0.80

OrientationMode = Literal["auto", "0", "180"]

# Orientacao decidida por diagrama em vez de um checkbox global (S-13).
DEFAULT_ORIENTATION_MODE: OrientationMode = "auto"

# A partir desta diferenca de min_confidence entre as duas orientacoes, a confianca decide
# sozinha. Medido no split de teste: a margem mediana e 0,925 e o percentil 5 e 0,220 --
# acima de 0,20 a confianca acertou 100% das 320 vezes.
ORIENTATION_DECISIVE_MARGIN = 0.20

# Abaixo da margem decisiva a confianca vira ruido (medido no 1937 Kemeri: duas leituras de
# pe corretas com margem de 0,001 e 0,019) e quem decide e a estrutura da posicao. Diferenca
# minima, em filas, entre o prior de peoes de uma orientacao e o da outra para ele decidir.
# 1,0 fila e conservador: nos casos reais medidos a diferenca foi de 5 a 8 filas.
ORIENTATION_PAWN_PRIOR_MARGIN = 1.0

# TTA (S-29): sete vistas por tabuleiro em vez de uma. Medido no split de teste: 316
# tabuleiros exatos contra 315, ou seja um em 320, por 6x o tempo de inferencia. Desligado.
TTA_ENABLED = False

# Aplicar ou nao a temperatura calibrada (S-28) que o checkpoint carrega.
#
# Desligado por medicao, nao por preguica. O temperature scaling minimiza a NLL de
# validacao e consegue: a NLL cai (0,00403 -> 0,00301 no res32). Mas as duas coisas que o
# produto usa pioram:
#
# | com T calibrado | ECE no teste | rejeitados pelo gate |
# |---|---|---|
# | phase5, T=1,00 | 0,000265 | 6 de 320 |
# | phase5, T=1,85 | 0,000837 | 12 de 320 |
#
# O motivo e estrutural: o modelo acerta 99,98% das casas, e ai confianca 0,9998 ja e
# quase perfeitamente calibrada. Achata-la afasta a confianca da verdade em vez de
# aproximar -- o ECE penaliza confianca abaixo do acerto tanto quanto acima. A NLL melhora
# porque e dominada pelas poucas casas erradas com confianca alta; o ECE, que pondera por
# contagem, vai no sentido oposto. Os dois objetivos discordam, e o que o criterio de
# aceite da S-28 nomeia e o ECE.
#
# O custo pratico de aplicar seria dobrar a fila de revisao sem corrigir uma casa. A
# temperatura continua sendo ajustada e gravada no checkpoint: ela e diagnostico util
# (T=1,85 diz que o modelo e confiante demais no sentido da NLL) e volta a valer se um dia
# a acuracia por casa cair para uma faixa em que haja erro de calibracao para corrigir.
APPLY_CALIBRATED_TEMPERATURE = False

# Teto de diagramas por pagina. Era 8, espalhado por seis arquivos, e cortava diagrama de
# verdade: as paginas de exercicio do "A Matter of Endgame Technique" tem grade 3x3, e o
# nono diagrama -- o de menor score entre nove praticamente empatados -- caia fora sem que
# nada avisasse.
#
# Medido em 6 livros, 15 paginas de cada, com teto 8 contra teto 30: so o Aagaard perde
# algo (55 contra 58 diagramas), e nenhum outro livro ganha um unico candidato a mais.
# Ou seja, subir o teto nao admite lixo -- quem filtra e o piso de score de `detect_boards`,
# nao este numero. 12 cobre grade 3x3 e 3x4 com folga.
DEFAULT_MAX_BOARDS = 12

ReadingOrder = Literal["row", "column"]

# Ordem em que os diagramas de uma pagina sao numerados (S-14). Um unico padrao aqui e o
# que faz o "diagrama 2" da GUI ser o mesmo do header [Diagram "2"] no PGN: antes a GUI
# usava "row" (padrao de detect_boards) e a exportacao passava "column", e numa pagina de
# duas colunas a numeracao divergia -- justamente quando o usuario quer conferir.
# "column" e o correto para a maioria dos livros de xadrez, que sao de duas colunas.
DEFAULT_READING_ORDER: ReadingOrder = "column"

# Quantos tabuleiros 800x800x3 (1,83 MiB cada) ficam residentes no cache do dataset
# (S-26). O cache sem teto de antes carregava os 3.208 e chegava a 6,11 GiB de pico de
# RSS medidos.
#
# A S-26 sugere 256 (~470 MiB). Medido, 256 e desperdicio: com BoardGroupedSampler o
# conjunto de trabalho e uma janela de BOARDS_PER_CHUNK tabuleiros, e cada tabuleiro e
# lido uma vez por epoca com cache >= a janela. De 128 para 256 a taxa de acerto nao
# muda -- so a memoria. 2x a janela deixa folga para o prefetch do DataLoader, que le
# adiante e pode atravessar a fronteira de duas janelas.
DEFAULT_BOARD_CACHE_SIZE = 128

# O loader de validacao roda com shuffle=False, e ai o acesso ja e estritamente
# sequencial: um cache de 1 tabuleiro basta para 98% de acerto (ha teste para isso).
# Dar a validacao o mesmo cache do treino dobrava a memoria residente sem trocar por nada.
VAL_BOARD_CACHE_SIZE = 4

# Tabuleiros cujas casas sao embaralhadas juntas pelo BoardGroupedSampler (S-26).
# O valor equilibra duas coisas em tensao: quanto menor, menor o conjunto de trabalho do
# cache; quanto maior, mais misturado o lote -- e um lote de 128 casas vindo de 2
# tabuleiros so tem a estatistica de 2 posicoes, o que deixa o BatchNorm ruidoso.
# 64 tabuleiros sao 117 MiB residentes e 4.096 casas por janela.
BOARDS_PER_CHUNK = 64

# --- ÚNICA divergência em relação ao upstream (ver _vendor/__init__.py) ---------------
#
# Lá `PROJECT_ROOT` é a raiz do ChessVisionOFF_Puro e aponta para `data/` e `PDF/`, que
# não existem aqui: este projeto usa o pacote só para inferência. `parents[3]` sobe de
# `src/chess_pdf_editor/local_ocr/_vendor/` até `src/chess_pdf_editor/`, e daí o modelo
# é procurado pela cadeia de `local_ocr.default_model_path()` — variável de ambiente,
# preferência do usuário, `models/` do repositório —, não por este caminho fixo.
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "piece_classifier.pt"
