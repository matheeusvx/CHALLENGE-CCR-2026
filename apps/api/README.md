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
`SPATIAL_REGULARIZATION_ENABLED=true` adiciona a representacao operacional V2,
preservando a segmentacao raw e sem novas consultas externas.

No contrato tecnico, `score_gt_30_cm` e o campo canonico e
`calibration_status` e `uncalibrated`. `probability_gt_30_cm` permanece como
alias deprecated para compatibilidade com historicos/clientes v0; nao representa
probabilidade calibrada.

## Endpoints

- `GET /api/health`
- `POST /api/analyses/validate-geometry`
- `POST /api/analyses/run`
- `GET /api/analyses/{analysis_id}/artifacts/{artifact_name}`

No fluxo web, `POST /api/analyses/run` precisa receber apenas `geometry`. A API
preenche o perfil interno `roadside_grass_default` e calcula `end_date` com a
data atual do timezone operacional; `start_date` e o mesmo dia do mes calendario
anterior, ajustado corretamente em finais de mes. A resposta registra esses
valores em `analysis_period`.

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
