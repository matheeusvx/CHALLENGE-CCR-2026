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
`API_OUTPUT_ROOT`.

## Endpoints

- `GET /api/health`
- `POST /api/analyses/validate-geometry`
- `POST /api/analyses/run`
- `GET /api/analyses/{analysis_id}/artifacts/{artifact_name}`

O registro de analises e local ao processo. Reiniciar a API remove os
identificadores disponiveis, mas nao apaga os artefatos gravados no disco.

## Testes

```powershell
python -m pytest -q apps\api\tests --basetemp=.pytest_tmp_api -p no:cacheprovider
```

Os testes substituem o servico por injecao de dependencia e nao consultam cenas
Sentinel-2 reais.
