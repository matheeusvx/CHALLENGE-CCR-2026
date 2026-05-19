# Classificação de Vegetação em Rodovia

Projeto acadêmico em Python para classificar imagens de vegetação em **faixa de domínio comum de rodovia** em duas classes:

- `cortar`
- `nao_cortar`

O objetivo é manter uma base simples, organizada e fácil de explicar, usando **OpenCV** para leitura de imagens e **PyTorch/torchvision** para **transfer learning**.

---

## Estrutura do projeto

```text
.
├── data/
│   ├── train/
│   │   ├── cortar/
│   │   └── nao_cortar/
│   ├── val/
│   │   ├── cortar/
│   │   └── nao_cortar/
│   └── test/
│       ├── cortar/
│       └── nao_cortar/
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

---

## Preparação do ambiente

Crie e ative um ambiente virtual:

```bash
python -m venv .venv
```

### No Windows
```bash
.venv\Scripts\activate
```

### No Linux/macOS
```bash
source .venv/bin/activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

---

## Organização dos dados

Coloque as imagens locais nas pastas abaixo:

```text
data/
├── train/
│   ├── cortar/
│   └── nao_cortar/
├── val/
│   ├── cortar/
│   └── nao_cortar/
└── test/
    ├── cortar/
    └── nao_cortar/
```

> Este repositório não inclui imagens. Use um conjunto de dados próprio, já separado em treino, validação e teste.

---

## Treinamento

Execute o treinamento com:

```bash
python src/train.py --data-dir data --model-path models/modelo_vegetacao.pth --image-size 224 --batch-size 8 --epochs 5
```

O modelo treinado será salvo em:

```text
models/modelo_vegetacao.pth
```

---

## Avaliação

Após treinar o modelo, execute:

```bash
python src/evaluate.py --data-dir data --model-path models/modelo_vegetacao.pth --split test --image-size 224
```

O script exibe métricas de classificação e salva a matriz de confusão em:

```text
outputs/matriz_confusao_test.png
```

---

## Predição de uma imagem

Para classificar uma imagem individual:

```bash
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth --image-size 224
```

Para mostrar a imagem processada usada na inferência:

```bash
python src/predict.py --image caminho/para/imagem.jpg --model-path models/modelo_vegetacao.pth --image-size 224 --show-image
```

### Saída esperada no terminal

```text
Imagem: imagem.jpg
Classe prevista: cortar
Confianca: 92.4%
```

---

## Observações

- O projeto considera apenas o cenário de **faixa de domínio comum**.
- A classificação é **binária**.
- O pipeline espera imagens locais organizadas por pasta de classe.
- Não há scraping, automação física ou integração com aplicativo mobile.
- A base foi criada para fins acadêmicos e pode ser expandida conforme a necessidade do trabalho.
