# Web

Frontend Next.js da Motiva Vegetation Intelligence. A primeira tela permite
colar uma AOI GeoJSON, validar a geometria pela API e executar o pipeline real
de forma sincrona.

## Execucao local

```powershell
Copy-Item apps\web\.env.example apps\web\.env.local
pnpm install
pnpm --dir apps\web generate:api
pnpm --dir apps\web dev
```

A aplicacao abre em `http://localhost:3000`. Defina `NEXT_PUBLIC_API_URL` quando
a API nao estiver em `http://localhost:8000`.

## Organizacao

- `app/`: App Router, layout, providers e estilos globais.
- `components/analysis/`: formulario, estados, recomendacao, metricas, grafico e cenas.
- `lib/api/`: cliente HTTP centralizado e tipos gerados do OpenAPI.
- `lib/schemas/`: validacao Zod dos dados criticos.
- `stores/`: rascunho do formulario e estado visual compartilhado.
- `tests/`: testes Vitest e Testing Library com rede controlada.

TanStack Query gerencia healthcheck, validacao e execucao. Zustand nao duplica
as respostas remotas.

## Qualidade

```powershell
pnpm --dir apps\web lint
pnpm --dir apps\web test
pnpm --dir apps\web build
```
