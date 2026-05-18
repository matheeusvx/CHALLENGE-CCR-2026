<<<<<<< HEAD
# Classificação de Vegetação em Rodovia

Projeto acadêmico em Python para classificar imagens de vegetação em faixa de domínio comum de rodovia em duas classes:
=======
# Classificacao de Vegetacao em Rodovia

Projeto academico em Python para classificar imagens de vegetacao em faixa de dominio comum de rodovia em duas classes:
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

- `cortar`
- `nao_cortar`

<<<<<<< HEAD
O objetivo é manter uma base simples, organizada e fácil de explicar, usando OpenCV para leitura de imagens e PyTorch/torchvision para transfer learning.
=======
O objetivo e manter uma base simples, organizada e facil de explicar, usando OpenCV para leitura de imagens e PyTorch/torchvision para transfer learning.
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

## Estrutura do projeto

```text
.
<<<<<<< HEAD
├── data/
│   ├── cortar/
│   └── nao_cortar/
├── models/
├── notebooks/
├── outputs/
├── src/
│   ├── dataset.py
│   ├── evaluate.py
│   ├── predict.py
│   ├── train.py
│   └── utils.py
├── README.md
└── requirements.txt
```

## Preparação do ambiente
=======
+-- data/
|   +-- train/
|   |   +-- cortar/
|   |   +-- nao_cortar/
|   +-- val/
|   |   +-- cortar/
|   |   +-- nao_cortar/
|   +-- test/
|       +-- cortar/
|       +-- nao_cortar/
+-- models/
+-- notebooks/
+-- outputs/
+-- src/
|   +-- dataset.py
|   +-- evaluate.py
|   +-- predict.py
|   +-- train.py
|   +-- utils.py
+-- README.md
+-- requirements.txt
```

## Preparacao do ambiente
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

Crie e ative um ambiente virtual:

```bash
python -m venv .venv
<<<<<<< HEAD
source .venv/bin/activate
```

No Windows, use:
=======
```

No Windows:
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

```bash
.venv\Scripts\activate
```

<<<<<<< HEAD
Instale as dependências:
=======
No Linux/macOS:

```bash
source .venv/bin/activate
```

Instale as dependencias:
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

```bash
pip install -r requirements.txt
```

<<<<<<< HEAD
## Organização dos dados

Coloque as imagens nas pastas abaixo:

```text
data/
├── cortar/
│   ├── imagem_001.jpg
│   └── imagem_002.jpg
└── nao_cortar/
    ├── imagem_003.jpg
    └── imagem_004.jpg
```

> Este repositório não inclui imagens. Use um conjunto de dados próprio, organizado nas duas classes do projeto.

## Treinamento

Execute o treinamento com transfer learning:

```bash
python src/train.py --data-dir data --model-path models/modelo_vegetacao.pth
```

O modelo treinado será salvo em `models/modelo_vegetacao.pth`.

## Avaliação

Após treinar o modelo, execute:

```bash
python src/evaluate.py --data-dir data --model-path models/modelo_vegetacao.pth
```

O script exibe métricas de classificação e salva a matriz de confusão em `outputs/matriz_confusao.png`.

## Predição de uma imagem
=======
## Organizacao dos dados

Coloque as imagens locais nas pastas abaixo:

```text
data/
+-- train/
|   +-- cortar/
|   +-- nao_cortar/
+-- val/
|   +-- cortar/
|   +-- nao_cortar/
+-- test/
    +-- cortar/
    +-- nao_cortar/
```

Este repositorio nao inclui imagens. Use um conjunto de dados proprio, ja separado em treino, validacao e teste.

## Treinamento

```bash
python src/train.py --data-dir data --model-path models/modelo_vegetacao.pth --image-size 224 --batch-size 8 --epochs 5
```

O modelo treinado sera salvo em `models/modelo_vegetacao.pth`.

## Avaliacao

```bash
python src/evaluate.py --data-dir data --model-path models/modelo_vegetacao.pth --split test --image-size 224
```

O script exibe metricas de classificacao e salva a matriz de confusao em `outputs/matriz_confusao_test.png`.

## Predicao de uma imagem
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)

Para classificar uma imagem individual:

```bash
<<<<<<< HEAD
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth
```

## Observações

- O projeto considera apenas o cenário de faixa de domínio comum.
- A classificação é binária.
- Não há scraping, automação física ou integração com aplicativo mobile.
- A base foi criada para fins acadêmicos e pode ser expandida conforme a necessidade do trabalho.
=======
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth --image-size 224
```

Para mostrar a imagem processada usada na inferencia:

```bash
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth --image-size 224 --show-image
```

A saida esperada no terminal segue este formato:

```text
Imagem: imagem.jpg
Classe prevista: cortar
Confianca: 92.4%
```

## Observacoes

- O projeto considera apenas o cenario de faixa de dominio comum.
- A classificacao e binaria.
- O pipeline espera imagens locais organizadas por pasta de classe.
- Nao ha scraping, automacao fisica ou integracao com aplicativo mobile.
- A base foi criada para fins academicos e pode ser expandida conforme a necessidade do trabalho.
>>>>>>> bc5df2a (feat: estrutura inicial do MVP com pipeline de classificacao binaria)
