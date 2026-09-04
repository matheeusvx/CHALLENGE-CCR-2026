# Monitoramento de Vegetacao por Satelite

Projeto academico para acompanhar vigor e mudancas da vegetacao em areas de
rodovia por meio de uma serie temporal real de NDVI. O MVP consulta cenas
Sentinel-2 L2A no Microsoft Planetary Computer, recorta a area informada e
exporta os resultados calculados.

O pipeline tambem produz uma recomendacao experimental e explicavel para o
cenario inicial de faixa lateral gramada comum: `cortar`, `nao_cortar` ou
`inconclusivo`. Ela usa exclusivamente o historico local de NDVI, tendencia,
quedas, persistencia e qualidade das observacoes. O classificador anterior de
fotografias RGB permanece preservado em `src/legacy/photo_classifier/`.

## Plataforma web

O repositorio tambem contem a fundacao da plataforma **Motiva Vegetation
Intelligence**, organizada como um monorepo sem duplicar o motor Python:

- `src/satellite_monitoring/service.py`: servico reutilizavel chamado pela CLI e pela API;
- `apps/api`: FastAPI sincrona, contratos Pydantic e acesso controlado aos artefatos;
- `apps/web`: Next.js, TypeScript, TanStack Query, Zod, Zustand e Apache ECharts;
- `packages/contracts`: OpenAPI gerado e fluxo de tipos TypeScript;
- `infra`: reserva para os modulos de infraestrutura posteriores;
- `docker-compose.yml`: composicao local inicial com apenas `api` e `web`.

O fluxo web apresenta um mapa operacional MapLibre: o operador navega ate o
trecho, desenha ou edita um Polygon, valida a AOI com a mesma logica do pipeline,
executa a analise real e recebe recomendacao, contexto e evolucao temporal em
uma visao operacional simplificada.
A entrada manual e o upload GeoJSON continuam disponiveis em uma secao avancada.
A API nao chama a CLI por subprocesso.

Os detalhes de cenas, scores, pixels, thresholds e metricas intermediarias nao
sao exibidos ao operador, mas permanecem na API e nos artefatos de auditoria.

O operador percorre apenas as etapas **Area** e **Resultado**. A API aplica um
perfil operacional interno para faixa lateral gramada e calcula o periodo da
analise automaticamente: da data atual ate um mes calendario anterior, usando
`America/Sao_Paulo` por padrao. Datas e parametros tecnicos continuam disponiveis
na CLI e em chamadas legadas da API, mas nao sao controles da interface web.

A base operacional padrao usa um StyleSpecification raster local com tiles do
OpenStreetMap e inicia em Louveira no zoom 12. A opcao Terreno permanece
temporariamente indisponivel. A AOI possui source e layers proprios,
independentes do editor TerraDraw, e e reinstalada depois do carregamento do
style.

### Execucao sem Docker

Terminal 1, na raiz, em PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r apps\api\requirements.txt
$env:API_CORS_ORIGINS = "http://localhost:3000"
python -m uvicorn apps.api.app.main:app --reload --port 8000
```

Terminal 2:

```powershell
Copy-Item apps\web\.env.example apps\web\.env.local
pnpm install
pnpm --dir apps\web generate:api
pnpm --dir apps\web dev
```

A API fica em `http://localhost:8000`, sua documentacao em
`http://localhost:8000/docs` e o frontend em `http://localhost:3000`.

### Execucao com Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

O Compose aguarda o healthcheck da API antes de iniciar o frontend. Nao ha
segredos nas imagens; os arquivos `.env` reais permanecem ignorados.

## Historico operacional e banco de dados

As analises executadas passam a ser gravadas em um banco relacional, junto com os
dados de referencia fornecidos pela CCR Motiva. A decisao continua sendo produzida
exclusivamente pelo motor de satelite: o banco apenas registra o resultado.

O padrao e SQLite em arquivo (`data/motiva.db`), sem servico externo. Como todo o
acesso passa por SQLAlchemy, trocar `DATABASE_URL` por um PostgreSQL nao exige
alteracao de codigo.

### Tabelas

Referencia, semeada com os arquivos originais do desafio:

- `highway`, `km_marker`: rodovia e marcos quilometricos georreferenciados;
- `cross_section_position`: as posicoes transversais do unifilar (itens 1.1 a 1.12),
  cada uma com o limite contratual de altura aplicavel ao local;
- `segment`: celula de 500 m por posicao transversal;
- `field_condition_observation`: classe de altura observada em campo por segmento e data;
- `mowing_polygon`: poligonos de classificacao de rocada com o metodo exigido.

Operacional, gravada a cada execucao:

- `analysis`: decisao, confianca, periodo, areas, qualidade, geometria da AOI e a
  resposta completa da API;
- `analysis_observation`: a serie temporal NDVI usada na analise.

### Limites contratuais de altura

As classes do unifilar (`1` = h < 10 cm, `2` = 10 a 30 cm, `3` = h > 30 cm) refletem
dois limites regulatorios distintos, e o limite aplicavel depende do local:

- **30 cm** na faixa de dominio em geral, largura minima de 4 m
  (ARTESP Anexo 06, item b.1.1; ANTT PER p. 31, item 6);
- **10 cm** em areas nobres como acessos, trevos, pracas de pedagio e entornos de
  instalacoes operacionais, largura minima de 10 m
  (ARTESP Anexo 06, item b.1.1; ANTT PER p. 31, item 3).

Por isso `cross_section_position.height_limit_cm` guarda o limite por posicao: a
classe 2 ja e nao conformidade em um dispositivo, mas nao na faixa lateral comum.

### Ingestao dos dados da CCR

Os arquivos originais nao sao versionados. Aponte o script para a pasta extraida:

```powershell
python -m scripts.ingest_motiva_data --data-dir "C:\caminho\Arquivos - Dados challenge MOTIVA"
```

A ingestao e idempotente: recria as tabelas de referencia a cada execucao e nunca
toca no historico de analises.

### Endpoints de historico

- `GET /api/analyses`: lista paginada, da mais recente para a mais antiga, com
  filtros `limit`, `offset` e `decision`;
- `GET /api/analyses/{analysis_id}`: analise completa gravada, incluindo a
  geometria da AOI, usada pelo frontend para reabrir uma analise anterior.

## Apoio a decisao por historico (ML)

Sobre o banco roda um modelo de apoio, `regrowth-support-v0`, que estima a
probabilidade de a vegetacao de um trecho estar acima do limite contratual do
local a partir da ultima condicao observada em campo.

**O modelo nao decide.** A recomendacao continua sendo produzida exclusivamente
por `cut_recommendation.recommend_cut`. O apoio entra na resposta da API como o
campo aditivo `decision_support` e e util sobretudo quando o satelite devolve
confianca baixa ou `inconclusivo` — inclusive na faixa morta entre os percentis
50 e 75, em que a regra nunca conclui.

### Como funciona

O alvo de treino sao as transicoes observadas entre levantamentos de campo
consecutivos: cada celula do unifilar vira um par (condicao em t, condicao em
t+1), e o rotulo e o descumprimento do limite do local no segundo levantamento.
As features sao apenas duas, a classe de altura atual e o limite contratual da
posicao transversal, porque o volume de exemplos positivos nao sustenta um
modelo maior.

```bash
python -m scripts.train_regrowth_support
```

O artefato vai para `models/regrowth_support_v0.json` com metricas, contagens e
limitacoes registradas.

### Degradacao explicita

O apoio prefere se calar a inventar certeza. O campo `status` assume:

- `available` — ha vistoria recente e o modelo opina;
- `stale_field_data` — ha historico, porem antigo demais para extrapolar; a
  resposta traz apenas o retrato descritivo da ultima vistoria;
- `insufficient_history` — nao ha vistoria para o quilometro da area analisada;
- `unavailable` — modelo ausente ou falha ao avaliar.

O horizonte empirico do modelo e o intervalo entre os levantamentos disponiveis.
Passado `MAX_FIELD_DATA_AGE_DAYS`, a extrapolacao perde base e o apoio deixa de
sugerir decisao.

### Limitacoes atuais

Com os dois levantamentos disponiveis ate aqui (2026-03-13 e 2026-03-20), o
conjunto de treino tem 248 transicoes e apenas 19 exemplos positivos. A
validacao usa `StratifiedGroupKFold` por quilometro, para que segmentos vizinhos
nao caiam em lados opostos da particao. As metricas registradas no artefato
devem ser lidas com essa amostra em mente: o recall e alto e a precisao e baixa,
ou seja, o modelo sinaliza mais nao conformidades do que realmente ocorrem.

Cada novo levantamento entregue pela concessionaria amplia o conjunto de treino
e o horizonte util do modelo. O ganho maior viria de alinhar temporalmente a
serie NDVI de cada segmento com a classe observada em campo, o que transformaria
o apoio em um preditor de altura de fato.

### Dataset NDVI x condicao de campo

`scripts/build_ndvi_field_dataset.py` monta o conjunto que ligaria a leitura de
satelite ao ground truth da concessionaria. Para cada par (quilometro, data de
vistoria):

* a AOI e a uniao dos poligonos de rocada daquele km, area de vegetacao real
  informada pela CCR, e nao um buffer arbitrario;
* as features vem da cena Sentinel-2 valida mais proxima da vistoria, extraidas
  pelo mesmo caminho do pipeline (`datasets.training_scenes`), de modo que so
  entram cenas que passariam nos portoes de qualidade de uma analise real;
* o rotulo vem do unifilar da mesma data, agregado no quilometro.

```bash
python -m scripts.build_ndvi_field_dataset
```

O resultado vai para a tabela `ndvi_field_sample` e a execucao e incremental.

A janela de alinhamento e limitada automaticamente a menos da metade do menor
intervalo entre vistorias. Sem esse limite, a mesma cena Sentinel-2 pode ficar
mais proxima de duas vistorias diferentes e gerar linhas com features identicas
e rotulos distintos.

### Resultado da avaliacao: sem sinal na resolucao quilometrica

Com os dados disponiveis, **o NDVI agregado por quilometro nao prediz a condicao
observada em campo**. Sobre as amostras construidas a partir das vistorias de
2026-03-13 e 2026-03-20:

Sobre 38 amostras (24 com alguma nao conformidade):

| Rotulo | AUC do NDVI |
|---|---|
| Qualquer nao conformidade no km | 0.571 |
| Existe classe 3 (h > 30 cm) no km | 0.468 |
| Classe 3 apenas onde o limite e 30 cm | 0.468 |

A correlacao entre NDVI e a fracao de pontos nao conformes e de -0.06, ou seja,
praticamente nula. A variacao de NDVI entre as duas datas tambem nao acompanha
as rocadas observadas: a correlacao com o numero de celulas cortadas e +0.14,
com o sinal invertido em relacao ao esperado, ja que rocar deveria reduzir o
NDVI.

A causa mais provavel e incompatibilidade de resolucao, e nao falha de modelo:

* o rotulo e por posicao transversal, enquanto o NDVI e a media de todas as
  posicoes daquele quilometro;
* a agregacao do rotulo e do tipo "existe alguma nao conformidade", enquanto a
  feature e uma media, o que e estruturalmente incompativel;
* faixas laterais costumam ser mais estreitas que o pixel de 10 m do Sentinel-2,
  entao o valor lido mistura vegetacao, asfalto e solo.

Isso e coerente com o `height_estimator_v0`, que ja registrava ROC-AUC 0.585 no
proprio artefato. Os dois caminhos batem no mesmo teto.

Por isso **nenhum modelo foi treinado sobre esse conjunto**: seria um modelo sem
sinal. A infraestrutura fica pronta e o mesmo comando volta a ser util assim que
uma destas condicoes existir:

1. o vinculo entre poligono e posicao transversal, que depende do eixo da
   rodovia; com ele o rotulo passa a descrever exatamente a area medida;
2. imagens de resolucao maior que a largura das faixas, como PlanetScope ou
   levantamento aereo;
3. mais vistorias, que permitem trabalhar com variacao temporal por trecho em
   vez de comparacao entre trechos.

### Variaveis

- `API_HOST` e `API_PORT`: bind da API;
- `API_CORS_ORIGINS`: origens permitidas, separadas por virgula;
- `API_OUTPUT_ROOT`: raiz dos artefatos gerados;
- `DATABASE_URL`: banco do historico operacional (padrao `sqlite:///data/motiva.db`);
- `ANALYSIS_TIMEZONE`: timezone da data oficial da analise (padrao `America/Sao_Paulo`);
- `MULTISOURCE_ENABLED`, `GEDI_ENABLED` e `ICESAT2_ENABLED`: fundacao futura,
  desabilitada por padrao e sem efeito sobre o baseline Sentinel-2;
- `MULTISOURCE_FUSION_MODE`: aceita `disabled` ou `shadow`; nenhum modo altera
  a decisao nesta etapa;
- `NEXT_PUBLIC_API_URL`: URL publica usada pelo navegador.

### Workspace geoespacial

No frontend, selecione a ferramenta de poligono, clique sobre a faixa lateral
para adicionar vertices e clique no primeiro ponto para concluir. A ferramenta
de edicao permite mover e remover vertices; tambem existem controles para
desfazer, refazer, excluir e enquadrar a AOI. A aba **Area** mostra uma previa
Turf e, depois de **Validar area**, substitui esses valores pelos metadados
oficiais retornados pela API.

Qualquer edicao incrementa a revisao da geometria, descarta a validacao anterior
e bloqueia **Executar analise** ate uma nova validacao. As abas **Area** e
**Resultado** compartilham o mesmo estado, por isso alternar entre elas nao
remove a geometria. A recomendacao altera o contorno no mapa e tambem e
apresentada em texto, com confianca e periodo oficial retornado pela API.

### Perfil operacional web

Os valores abaixo formam o perfil interno `roadside_grass_default`. Eles sao
experimentais, preservam o comportamento atual e nao representam parametros
validados operacionalmente pela Motiva:

- `max_cloud_cover = 30`, `max_candidate_scenes = 40`, `max_scenes = 12`;
- `scene_order = newest`, `min_valid_pixel_percentage = 70`;
- `min_valid_pixel_count = 30`, `min_aoi_coverage_percentage = 95`;
- `min_observations = 4`;
- `daily_aggregation = best`, `decision_min_observations = 4`;
- `high_vegetation_percentile = 75`, `significant_drop_absolute = 0.06`;
- `significant_drop_relative_percentage = 15`, `trend_window = 3`;
- `max_gap_days = 20`, `recent_intervention_days = 20`.

Os parametros continuam no motor e na CLI. A centralizacao na API prepara a
criacao futura de perfis distintos por cenario sem expor thresholds ao operador.

Ao concluir o desenho, aplicar ou importar GeoJSON, abrir o resultado ou usar
**Enquadrar**, a camera ajusta a area com padding para os controles e zoom
maximo 16. O mapa nao executa movimentos continuos durante o arraste de
vertices. O contorno persistente combina preenchimento transparente, halo de
contraste, linha semantica, vertices e rotulo textual.

A secao **Entrada avancada por GeoJSON** aceita Polygon, Feature Polygon ou uma
FeatureCollection com um unico Polygon. Arquivos `.json` e `.geojson` de ate
2 MB sao lidos localmente no navegador; somente a geometria normalizada e
enviada a API. Use coordenadas EPSG:4326 no formato `[longitude, latitude]`.

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
 estatisticas + consolidacao diaria NDVI
                  |
                  v
    regras explicaveis experimentais
                  |
                  v
       CSV + JSON + grafico + decisao
```

A fonte e a API STAC publica do
[Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/api/stac/v1),
na colecao `sentinel-2-l2a`. Os assets privados sao assinados no momento da
consulta com `planetary_computer.sign_inplace`.

## Estrutura

```text
.
+-- apps/
|   +-- api/
|   +-- web/
+-- data/
+-- infra/
+-- models/
+-- notebooks/
+-- outputs/
|   +-- satellite_monitoring/
+-- scripts/
|   +-- smoke_test.ps1
+-- packages/
|   +-- contracts/
+-- src/
|   +-- legacy/
|   |   +-- photo_classifier/
|   +-- satellite_monitoring/
|       +-- cli.py
|       +-- config.py
|       +-- cut_recommendation.py
|       +-- geometry.py
|       +-- indices.py
|       +-- outputs.py
|       +-- quality.py
|       +-- temporal_quality.py
|       +-- raster_processing.py
|       +-- service.py
|       +-- stac_client.py
+-- tests/
|   +-- test_cli.py
|   +-- test_cut_recommendation.py
|   +-- test_geometry.py
|   +-- test_indices.py
|   +-- test_outputs.py
|   +-- test_quality.py
|   +-- test_stac_client.py
+-- README.md
+-- docker-compose.yml
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
  --min-observations 4 `
  --daily-aggregation best
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
  --min-observations 4 `
  --daily-aggregation best `
  --decision-min-observations 4 `
  --high-vegetation-percentile 75 `
  --significant-drop-absolute 0.06 `
  --significant-drop-relative-percentage 15 `
  --trend-window 3 `
  --max-gap-days 20 `
  --recent-intervention-days 20
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
argumentos, incluindo `--output-dir`. O pipeline considera ate 40 candidatas
dentro da janela, avalia a qualidade local e somente entao aplica o limite final
de 12 cenas. CSV e grafico permanecem em ordem cronologica.

Por padrao, `--daily-aggregation best` mantem uma observacao por dia. A cena
aprovada com maior `scene_quality_score` e escolhida; os desempates usam maior
percentual valido, maior cobertura da AOI, menor nuvem global e, por fim, o ID
da cena. `median` calcula a mediana das estatisticas aceitas
do dia. `none` preserva todas as cenas aceitas. A proveniencia, as cenas
consideradas e a escolha de cada dia ficam registradas no CSV e no resumo.

Cada execucao cria uma pasta com horario proprio:

```text
outputs/satellite_monitoring/AAAAMMDD_HHMMSS/
+-- aoi.geojson
+-- scenes.csv
+-- ndvi_timeseries.csv
+-- raw_daily_timeseries.csv
+-- quality_report.json
+-- summary.json
+-- ndvi_timeseries.png
+-- cut_recommendation.json
+-- cut_recommendation.csv
```

- `aoi.geojson`: geometria efetivamente usada na consulta STAC e no recorte das
  bandas. Para entrada por arquivo, tambem preserva o documento original em
  `source_geojson`.
- `scenes.csv`: metadados, contagem de pixels, qualidade, motivos, aceite e
  status de processamento de cada cena selecionada.
- `ndvi_timeseries.csv`: serie robusta usada pela recomendacao, com estatisticas
  NDVI, qualidade, cobertura e proveniencia da agregacao diaria.
- `raw_daily_timeseries.csv`: observacoes diarias aceitas antes do diagnostico
  temporal, inclusive as posteriormente marcadas como suspeitas.
- `quality_report.json`: candidatas, cenas processadas e rejeitadas, estatisticas
  SCL, outliers, series bruta e analitica e qualidade global da analise.
- `summary.json`: parametros, metadados da area, endpoint, colecao, contagens, IDs STAC reais,
  estrategia temporal, limiares, descartes pelo limite, rejeicoes de qualidade,
  consolidacao diaria, recomendacao, intervalo processado, alertas e erros.
- `ndvi_timeseries.png`: serie diaria, mediana historica, quedas significativas,
  observacao atual e recomendacao, sempre com eixo NDVI entre -1 e 1.
- `cut_recommendation.json`: resultado explicavel completo, metricas, limites,
  qualidade, motivos, bloqueios e limitacoes.
- `cut_recommendation.csv`: uma linha resumida para integracao e auditoria.

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
interpolacao bilinear. A cobertura global `eo:cloud_cover` filtra candidatas,
mas nao e usada como mascara local. A composicao das classes SCL dentro da AOI
e registrada para auditoria. No perfil `roadside_grass`, uma cena sem SCL e
processada para rastreabilidade, mas rejeitada da serie principal.

Os limiares iniciais de qualidade sao provisorios:

- `high`: pelo menos 85% de pixels validos;
- `medium`: de 70% ate menos de 85%;
- `low`: menos de 70%.

O limite de aceite e configurado separadamente por
`--min-valid-pixel-percentage` (padrao 70). Sem a opcao
`--include-low-quality-scenes`, cenas abaixo desse limite permanecem em
`scenes.csv`, mas nao entram na serie temporal. O perfil operacional tambem
exige pelo menos 30 pixels validos, 95% de cobertura da AOI, SCL disponivel,
NDVI valido e ausencia de cobertura raster parcial relevante. Cobertura da AOI
e percentual de pixels validos sao metricas independentes.

O `scene_quality_score` varia de 0 a 100: validade local (30%), quantidade
absoluta (15%), cobertura da AOI (20%), disponibilidade SCL (15%), baixa
contaminacao SCL (10%) e baixa nuvem global (10%). O score nao substitui
`quality_reasons`. Reversoes isoladas so sao marcadas quando retornam
imediatamente ao patamar vizinho e possuem qualidade inferior aos dois vizinhos;
quedas persistentes de alta qualidade permanecem na analise.

O `analysis_quality` pondera media do score das cenas (30%), menor score (15%),
validade media (20%), cobertura media (15%), regularidade temporal (10%),
retencao apos outliers (5%) e aceite das cenas processadas (5%). Essa pontuacao
e experimental e nao representa validacao cientifica ou operacional.

O parametro `--min-observations` tem padrao 4. Quando a serie aceita fica abaixo
desse minimo, os arquivos ainda sao gerados com `overall_status` igual a
`insufficient_observations`; nenhuma interpretacao de tendencia e produzida.
Esses valores nao constituem validacao cientifica ou operacional.

## Recomendacao experimental

Os resultados significam:

- `cortar`: o NDVI atual esta no nivel alto do proprio historico local, com
  tendencia compativel, qualidade suficiente e sem queda recente confirmada;
- `nao_cortar`: ha indicio de intervencao recente confirmada ou a vegetacao esta
  abaixo do nivel historico alto, sem crescimento acelerado;
- `inconclusivo`: os dados nao sustentam nenhuma das duas decisoes com seguranca.

Uma recomendacao fica inconclusiva quando faltam observacoes, a serie possui
lacunas excessivas, a observacao mais recente esta distante do fim do periodo,
a qualidade ou cobertura da AOI e insuficiente, ha movimentos contraditorios,
nao e possivel calcular tendencia ou uma queda ainda aguarda confirmacao.

Os parametros iniciais sao configuraveis na CLI:

- `--decision-min-observations 4`;
- `--high-vegetation-percentile 75`;
- `--significant-drop-absolute 0.06`;
- `--significant-drop-relative-percentage 15`;
- `--trend-window 3`;
- `--max-gap-days 20`;
- `--recent-intervention-days 20`.

Esses limites sao hipoteses de engenharia experimentais e ainda nao foram
validados pela Motiva. A confianca `high`, `medium` ou `low` considera quantidade
e qualidade das observacoes, regularidade temporal, distancia dos limites,
confirmacao de queda, amplitude e estabilidade. Ela nao e uma probabilidade.

O resultado deve ser interpretado como apoio experimental para priorizacao de
inspecoes. O uso e restrito a areas predominantemente gramadas, delimitadas para
evitar pista, construcoes, arvores densas, corpos d'agua e areas urbanas. NDVI
nao mede altura em centimetros. A validacao exige comparar os resultados com
inspecoes de campo e registros reais de manutencao e corte.

## Testes

```powershell
python -m compileall src
python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider
python -m pytest -q apps\api\tests --basetemp=.pytest_tmp_api -p no:cacheprovider
pnpm --dir apps\web lint
pnpm --dir apps\web test
pnpm --dir apps\web build
docker compose config
```

Os testes unitarios usam pequenos arrays e series temporais deterministicas para
validar formula, mascaras, consolidacao, regras e serializacao. Eles nao fazem
chamadas externas nem gravam respostas inventadas da API.

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
- A recomendacao e experimental e pode retornar `inconclusivo`.
- O sistema nao detecta corte diretamente nem estima altura da vegetacao.
- Os resultados ainda nao representam validacao operacional da CCR/Motiva.
- Areas pequenas podem conter poucos pixels validos apos a mascara SCL.
- Disponibilidade de cenas e acesso aos assets dependem do servico externo.
- Os limiares `high`, `medium`, `low` e o minimo de observacoes sao provisoes
  de engenharia e ainda exigem calibracao com dados de campo.
- A API executa o pipeline de forma sincrona e guarda o registro de analises em memoria.
- Nao existem ainda autenticacao, persistencia, fila, worker ou processamento assincrono.
- O editor visual suporta apenas um Polygon ativo; MultiPolygon permanece
  disponivel no pipeline/CLI, mas nao no workspace desta etapa.
- O mapa ainda nao exibe rasters Sentinel-2, cobertura por cena ou cadastro de
  rodovias, e depende de acesso ao provedor de mapa base configurado.
- Limites municipais e rodovias administradas ainda nao possuem overlay
  proprio. A arquitetura reserva IDs para essas fontes, mas so aceitara dados
  oficiais, simplificados e versionados.
- A geometria e os resultados web ainda nao possuem persistencia historica.

Os proximos modulos recomendados sao a visualizacao cartografica da cobertura e
qualidade das cenas, persistencia do historico e metadados, processamento
assincrono com estados de execucao e autenticacao.
