# API

Backend FastAPI da Motiva Vegetation Intelligence. A API importa
`src.satellite_monitoring.service.run_monitoring_analysis` diretamente; ela nao
copia o motor, nao executa a CLI e nao interpreta texto de terminal.

## Execucao local

Na raiz do repositorio, em PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r apps\api\requirements.txt
$env:PYTHONPATH = (Get-Location).Path
python -m uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8000
```

A documentacao OpenAPI fica em `http://localhost:8000/docs`. As origens CORS e
o diretorio de saida sao configurados por `API_CORS_ORIGINS` e
`API_OUTPUT_ROOT`. `ANALYSIS_TIMEZONE` define o timezone da data oficial e usa
`America/Sao_Paulo` por padrao.

## Fundacao multissensor

O Sentinel-2 continua sendo o baseline exclusivo da analise. A fundacao
multissensor esta desabilitada por padrao; providers auxiliares serao opcionais
e executados de forma fail-soft, sem derrubar o baseline quando falharem. O modo
`shadow` esta reservado para validacao futura e ainda nao altera a decisao.

As flags `MULTISOURCE_ENABLED`, `GEDI_ENABLED` e `ICESAT2_ENABLED` usam `false`
por padrao. `MULTISOURCE_FUSION_MODE` aceita apenas `disabled` ou `shadow`.

A estimativa binaria de faixa de altura v0 tambem usa `false` por padrao. Para
habilita-la localmente antes de iniciar a API, defina
`$env:HEIGHT_ESTIMATION_ENABLED = "true"`. O resultado e evidencia experimental
adicional e nao altera a recommendation.

A segmentacao espacial shadow tambem fica desabilitada por padrao. Defina
`$env:SPATIAL_SEGMENTATION_ENABLED = "true"` para produzir zonas auxiliares
reutilizando os rasters ja processados; a recommendation global continua oficial.
O comprimento longitudinal experimental usa 50 m por padrao e pode ser ajustado
com `SPATIAL_SECTION_LENGTH_M`, sem alterar os thresholds da recommendation.
`SPATIAL_REGULARIZATION_ENABLED=true` adiciona a representacao operacional V2,
preservando a segmentacao raw e sem novas consultas externas.

No contrato tecnico, `score_gt_30_cm` e o campo canonico e
`calibration_status` e `uncalibrated`. `probability_gt_30_cm` permanece como
alias deprecated para compatibilidade com historicos/clientes v0; nao representa
probabilidade calibrada.

## Endpoints

- `GET /api/health`
- `POST /api/analyses/validate-geometry`
- `POST /api/analyses/jobs` (202: identificador e status)
- `GET /api/analyses/jobs/{job_id}` (polling no escopo autenticado)
- `POST /api/analyses/run` (sincrono, mantido para compatibilidade)
- `GET /api/analyses/{analysis_id}/artifacts/{artifact_name}`

No fluxo web, `POST /api/analyses/jobs` precisa receber apenas `geometry`. A API
preenche o perfil interno `roadside_grass_default` e calcula `end_date` com a
data atual do timezone operacional; `start_date` e o mesmo dia do mes calendario
anterior, ajustado corretamente em finais de mes. A resposta registra esses
valores em `analysis_period`.

A UI consulta o job a cada 2 segundos e mostra "Processando cenas Sentinel-2".
Os jobs manuais usam o executor da analise automatica e uma tabela separada no
mesmo SQLite (`AUTOMATIC_ANALYSIS_DB_PATH`). A mesma implementacao manual executa
o motor cientifico e grava historico/artefatos antes de publicar sucesso.
`completed` e `insufficient_observations` sao resultados validos; `failed` e erro.
Criacao e polling exigem o mesmo `operator_scope` autenticado. O limite de jobs
manuais pendentes segue `AUTO_ANALYSIS_MAX_CONCURRENT`, mesmo com automatica
desabilitada; o executor compartilhado limita o total de threads em processamento.

Para ampliar a janela sem mudar thresholds, defina no `.env` da API (ou no `.env`
da raiz carregado no inicio) `ANALYSIS_LOOKBACK_DAYS=60` e reinicie a API.
Manual e automatica usarao hoje menos 60 dias ate hoje no `ANALYSIS_TIMEZONE`.
Ausente/vazio preserva o mes calendario anterior; datas explicitas continuam
prevalecendo. `analysis_period.strategy` passa a `lookback_days`. As datas entram
na chave do cache automatico, portanto uma janela diferente nao reutiliza a antiga.

Execucao em threads e adequada ao ambiente local, mas nao e uma fila distribuida:
reiniciar o processo interrompe jobs ativos, sem retomada automatica. Status e
resultados terminais ficam no SQLite; jobs interrompidos expiram pelo TTL existente
(`AUTO_ANALYSIS_IN_PROGRESS_TTL_SECONDS`, 1800 por padrao) e retornam `JOB_TIMEOUT`.
O timeout nao interrompe uma thread Sentinel que ainda esteja em execucao.

Chamadas existentes ainda podem enviar datas e parametros explicitamente. Os
thresholds internos atuais sao experimentais: nuvens 30%, pool de 40 candidatas,
limite final de 12 cenas, 70% e pelo menos 30 pixels validos, 95% de cobertura
da AOI, SCL obrigatorio, 4 observacoes, agregacao `best`, percentil alto 75,
queda absoluta 0.06, queda relativa 15%, janela de tendencia 3 e janelas
temporais de 20 dias. Eles nao sao configuraveis pelo operador web.

Os artefatos `quality_report` e `raw_timeseries_csv` preservam a auditoria da
qualidade local e temporal; `timeseries_csv` continua representando a serie
efetivamente entregue ao motor de recomendacao.

O registro de analises e local ao processo. Reiniciar a API remove os
identificadores disponiveis, mas nao apaga os artefatos gravados no disco.

## Testes

```powershell
python -m pytest -q apps\api\tests --basetemp=.pytest_tmp_api -p no:cacheprovider
```

Os testes substituem o servico por injecao de dependencia e nao consultam cenas
Sentinel-2 reais.
