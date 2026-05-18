# Classificação de Vegetação em Rodovia

Projeto acadêmico em Python para classificar imagens de vegetação em faixa de domínio comum de rodovia em duas classes:

- `cortar`
- `nao_cortar`

O objetivo é manter uma base simples, organizada e fácil de explicar, usando OpenCV para leitura de imagens e PyTorch/torchvision para transfer learning.

## Estrutura do projeto

```text
.
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

Crie e ative um ambiente virtual:

```bash
python -m venv .venv
source .venv/bin/activate
```

No Windows, use:

```bash
.venv\Scripts\activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

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

Para classificar uma imagem individual:

```bash
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth
```

## Observações

- O projeto considera apenas o cenário de faixa de domínio comum.
- A classificação é binária.
- Não há scraping, automação física ou integração com aplicativo mobile.
- A base foi criada para fins acadêmicos e pode ser expandida conforme a necessidade do trabalho.
