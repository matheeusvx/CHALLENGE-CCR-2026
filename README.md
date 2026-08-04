# Monitoramento de Vegetacao por Satelite

Repositorio academico preparado para iniciar um MVP de monitoramento de vegetacao em rodovias usando dados reais de satelite.

Neste momento, a integracao com satelite ainda nao esta implementada. O classificador binario de fotos RGB locais foi preservado como codigo legado para consulta, treinamento e comparacao futura.

## Estrutura do projeto

```text
.
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
|   +-- legacy/
|   |   +-- photo_classifier/
|   |       +-- dataset.py
|   |       +-- evaluate.py
|   |       +-- predict.py
|   |       +-- train.py
|   |       +-- utils.py
|   +-- satellite_monitoring/
+-- README.md
+-- requirements.txt
```

## Preparacao do ambiente

Crie e ative um ambiente virtual:

```bash
python -m venv .venv
```

No Windows:

```bash
.venv\Scripts\activate
```

No Linux/macOS:

```bash
source .venv/bin/activate
```

Instale as dependencias:

```bash
pip install -r requirements.txt
```

## Classificador legado de fotos RGB

O classificador legado trabalha com imagens locais organizadas por classe:

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

Este repositorio nao inclui imagens. Use um conjunto de dados proprio e real, ja separado em treino, validacao e teste.

### Treinamento

```bash
python -m src.legacy.photo_classifier.train --data-dir data --model-path models/photo_classifier_resnet18.pth --image-size 224 --batch-size 8 --epochs 5
```

### Avaliacao

```bash
python -m src.legacy.photo_classifier.evaluate --data-dir data --model-path models/photo_classifier_resnet18.pth --split test --image-size 224
```

O script exibe metricas de classificacao e salva a matriz de confusao em `outputs/matriz_confusao_test.png`.

### Predicao de uma imagem

```bash
python -m src.legacy.photo_classifier.predict --image caminho/para/imagem.jpg --model-path models/photo_classifier_resnet18.pth --image-size 224
```

## Preparacao para o MVP de satelite

O pacote `src/satellite_monitoring` foi criado apenas como area de desenvolvimento futura. A proxima etapa deve definir a fonte real de dados orbitais, as bandas ou indices usados e o formato de entrada antes de qualquer implementacao.

## Observacoes

- Nao foram adicionados dados mockados.
- Nao foi implementada integracao com APIs ou provedores de satelite.
- O classificador RGB local continua disponivel em `src/legacy/photo_classifier`.
- As pastas `models/` e `outputs/` devem receber artefatos gerados localmente e nao versionados.
