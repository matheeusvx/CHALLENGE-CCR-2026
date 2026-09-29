# Contratos da API

`openapi.json` e gerado pelo FastAPI e representa o contrato compartilhado.
`apps/web/lib/api/generated.ts` e derivado desse arquivo com
`openapi-typescript`, fixado no `package.json` do frontend.

Depois de alterar schemas ou rotas:

```powershell
.\.venv\Scripts\python.exe -c "import json; from pathlib import Path; from apps.api.app.main import app; Path('packages/contracts/openapi.json').write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')"
pnpm --dir apps\web generate:api
```

O frontend mantem schemas Zod apenas para validar entradas e respostas criticas
em tempo de execucao; a definicao HTTP completa nao e mantida manualmente.
