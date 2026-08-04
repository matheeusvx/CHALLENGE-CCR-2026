# Monitoramento de Vegetacao por Satelite

Projeto academico para acompanhar vigor e mudancas da vegetacao em areas de
rodovia por meio de uma serie temporal real de NDVI. O MVP consulta cenas
Sentinel-2 L2A no Microsoft Planetary Computer, recorta a area informada e
exporta os resultados calculados.

Esta etapa nao decide se a vegetacao deve ser classificada como `cortar` ou
`nao_cortar`. O classificador anterior de fotografias RGB permanece preservado
em `src/legacy/photo_classifier/`.

## Fluxo do MVP

```text
circulo (latitude, longitude e raio) OU GeoJSON + datas
                           |
                           v
              area de interesse em EPSG:4326
                  |
                  v
     consulta STAC Sentinel-2 L2A
                  |
                  v
   vermelho + NIR + mascara SCL opcional
                  |
                  v
        estatisticas e serie NDVI
                  |
                  v
          CSV + JSON + grafico
```

A fonte e a API STAC publica do
[Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/api/stac/v1),
na colecao `sentinel-2-l2a`. Os assets privados sao assinados no momento da
consulta com `planetary_computer.sign_inplace`.

## Estrutura

```text
.
+-- data/
+-- models/
+-- notebooks/
+-- outputs/
|   +-- satellite_monitoring/
+-- scripts/
|   +-- smoke_test.ps1
+-- src/
|   +-- legacy/
|   |   +-- photo_classifier/
|   +-- satellite_monitoring/
|       +-- cli.py
|       +-- config.py
|       +-- geometry.py
|       +-- indices.py
|       +-- outputs.py
|       +-- quality.py
|       +-- raster_processing.py
|       +-- stac_client.py
+-- tests/
|   +-- test_cli.py
|   +-- test_geometry.py
|   +-- test_indices.py
|   +-- test_outputs.py
|   +-- test_quality.py
|   +-- test_stac_client.py
+-- README.md
+-- requirements.txt
```

## Instalacao

Use Python 3.11 ou 3.12. No Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

O pipeline precisa de acesso HTTPS ao Planetary Computer durante a consulta e
a leitura dos Cloud Optimized GeoTIFFs. Nenhuma cena completa e baixada por
padrao: `rasterio` solicita apenas os blocos da janela da area de interesse.

## Execucao

Exemplo com coordenadas fornecidas pela linha de comando:

```powershell
python -m src.satellite_monitoring.cli `
  --latitude -23.5505 `
  --longitude -46.6333 `
  --radius-meters 100 `
  --start-date 2026-06-01 `
  --end-date 2026-08-01 `
  --max-cloud-cover 20 `
  --max-scenes 12 `
  --scene-order newest `
  --min-valid-pixel-percentage 70 `
  --min-observations 4
```

Como alternativa, informe um arquivo GeoJSON que delimite somente a faixa
lateral da rodovia a ser analisada:

```powershell
python -m src.satellite_monitoring.cli `
  --geometry-file data/aoi/louveira_lateral.geojson `
  --start-date 2026-05-01 `
  --end-date 2026-08-04 `
  --max-cloud-cover 30 `
  --max-scenes 12 `
  --scene-order newest `
  --min-valid-pixel-percentage 70 `
  --min-observations 4
```

Os modos sao mutuamente exclusivos: use `--geometry-file` ou o conjunto
completo `--latitude`, `--longitude` e `--radius-meters`. O GeoJSON deve estar
em EPSG:4326, com coordenadas no formato `[longitude, latitude]`. Sao aceitos
`Polygon`, `MultiPolygon`, `Feature` poligonal e `FeatureCollection` nao vazia
contendo apenas geometrias poligonais. As geometrias de uma colecao sao unidas
para formar uma unica area; arquivos com outro CRS declarado, coordenadas fora
dos limites geograficos, geometrias vazias ou poligonos invalidos sao rejeitados.

O poligono deve representar apenas a area lateral relevante. Evite faixas
excessivamente estreitas: as bandas RED e NIR do Sentinel-2 usadas aqui possuem
resolucao espacial nominal de 10 metros, e pixels de borda podem misturar pista,
acostamento, vegetacao e areas vizinhas. A consulta pode usar a caixa envolvente
para otimizar a leitura, mas a mascara final respeita a geometria fornecida.

As coordenadas acima sao apenas um exemplo e nao estao fixas no codigo. Use
`python -m src.satellite_monitoring.cli --help` para consultar todos os
argumentos, incluindo `--output-dir`. Por padrao, as 12 cenas mais recentes
sao priorizadas; depois da selecao, CSV e grafico voltam a ordem cronologica.

Cada execucao cria uma pasta com horario proprio:

```text
outputs/satellite_monitoring/AAAAMMDD_HHMMSS/
+-- aoi.geojson
+-- scenes.csv
+-- ndvi_timeseries.csv
+-- summary.json
+-- ndvi_timeseries.png
```

- `aoi.geojson`: geometria efetivamente usada na consulta STAC e no recorte das
  bandas. Para entrada por arquivo, tambem preserva o documento original em
  `source_geojson`.
- `scenes.csv`: metadados, contagem de pixels, qualidade, motivos, aceite e
  status de processamento de cada cena selecionada.
- `ndvi_timeseries.csv`: media, mediana, desvio padrao, minimo, maximo e cobertura
  de pixels validos das cenas aceitas. Cenas abaixo do limite aparecem somente
  com `--include-low-quality-scenes` e continuam marcadas como `low`.
- `summary.json`: parametros, metadados da area, endpoint, colecao, contagens, IDs STAC reais,
  estrategia temporal, limiares, descartes pelo limite, rejeicoes de qualidade,
  intervalo efetivamente processado, alertas e erros por cena.
- `ndvi_timeseries.png`: evolucao temporal da media e mediana do NDVI.

Uma falha em uma cena e registrada e as cenas seguintes continuam. Falhas na
consulta ou uma execucao sem nenhuma cena processada retornam codigo diferente
de zero; o sistema nao substitui a falha por resultados ficticios.

## Qualidade dos pixels

Os assets vermelho e NIR sao encontrados primeiro pelo metadado
`eo:bands/common_name`; as chaves conhecidas `red`/`B04` e `nir`/`B08` sao
apenas fallback. Escala, offset e nodata de `raster:bands` sao aplicados quando
informados.

Quando o asset SCL existe, as classes abaixo sao removidas:

- `0`: nodata;
- `1`: pixel saturado ou defeituoso;
- `3`: sombra de nuvem;
- `8`: nuvem de probabilidade media;
- `9`: nuvem de probabilidade alta;
- `10`: cirrus fino;
- `11`: neve ou gelo.

O SCL e alinhado por vizinho mais proximo; a banda NIR e alinhada por
interpolacao bilinear. A cobertura global `eo:cloud_cover` filtra cenas, mas
nao e usada como mascara local. Se o SCL estiver ausente, a cena ainda e
processada e recebe um alerta de qualidade.

Os limiares iniciais de qualidade sao provisorios:

- `high`: pelo menos 85% de pixels validos;
- `medium`: de 70% ate menos de 85%;
- `low`: menos de 70%.

O limite de aceite e configurado separadamente por
`--min-valid-pixel-percentage` (padrao 70). Sem a opcao
`--include-low-quality-scenes`, cenas abaixo desse limite permanecem em
`scenes.csv`, mas nao entram na serie temporal. Ausencia de SCL, cobertura
parcial, nuvens globais excessivas e ausencia de NDVI valido ficam registradas
em `quality_reasons`.

O parametro `--min-observations` tem padrao 4. Quando a serie aceita fica abaixo
desse minimo, os arquivos ainda sao gerados com `overall_status` igual a
`insufficient_observations`; nenhuma interpretacao de tendencia e produzida.
Esses valores nao constituem validacao cientifica ou operacional.

## Testes

```powershell
python -m compileall src
python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider
```

Os testes unitarios usam apenas pequenos arrays locais para validar a formula,
mascaras e serializacao. Eles nao simulam uma execucao funcional nem gravam
respostas inventadas da API.

Para repetir manualmente um smoke test real no Windows, informe sua propria
area e um intervalo com imagens disponiveis:

```powershell
.\scripts\smoke_test.ps1 `
  -Latitude -23.10821 `
  -Longitude -46.96109 `
  -RadiusMeters 200 `
  -StartDate 2026-05-01 `
  -EndDate 2026-08-04 `
  -MaxCloudCover 30 `
  -MaxScenes 12 `
  -SceneOrder newest `
  -MinValidPixelPercentage 70 `
  -MinObservations 4
```

O script propaga o codigo de saida da CLI e nao contem coordenadas ou datas
padrao. Use `-IncludeLowQualityScenes` somente quando for necessario manter
observacoes abaixo do limite, sempre com a marcacao de qualidade correspondente.

## Classificador legado

O classificador local continua disponivel, sem alteracoes, nos comandos:

```powershell
python -m src.legacy.photo_classifier.train --data-dir data
python -m src.legacy.photo_classifier.evaluate --data-dir data --split test
python -m src.legacy.photo_classifier.predict --image caminho\imagem.jpg
```

Ele depende de imagens reais organizadas em `data/train`, `data/val` e
`data/test`, cada qual com as pastas `cortar` e `nao_cortar`. O repositorio nao
inclui imagens.

## Limitacoes

- NDVI nao mede diretamente a altura da grama em centimetros.
- A resolucao espacial do Sentinel-2 e limitada para faixas rodoviarias estreitas.
- Um pixel pode misturar vegetacao, pista, acostamento, solo e estruturas.
- A cobertura de nuvens do item nao substitui uma mascara local de pixels.
- Nesta etapa, o sistema monitora vigor e mudancas da vegetacao.
- A decisao `cortar` ou `nao_cortar` pertence a uma etapa posterior.
- Os resultados ainda nao representam validacao operacional da CCR/Motiva.
- Areas pequenas podem conter poucos pixels validos apos a mascara SCL.
- Disponibilidade de cenas e acesso aos assets dependem do servico externo.
- Os limiares `high`, `medium`, `low` e o minimo de observacoes sao provisoes
  de engenharia e ainda exigem calibracao com dados de campo.
