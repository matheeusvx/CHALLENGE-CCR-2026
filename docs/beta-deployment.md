# Deploy da Beta autenticada

O navegador acessa apenas o domínio Vercel. O `proxy.ts` encaminha `/api/*` ao
FastAPI usando `BACKEND_API_URL`, acrescenta `BETA_PROXY_SECRET` no servidor e
nunca entrega essas variáveis ao JavaScript do cliente. No Railway, somente
`/api/health` permanece público quando `BETA_AUTH_ENABLED=true`.

## Gerar as duas credenciais

Defina um pepper aleatório e execute, na raiz do repositório:

```powershell
$env:BETA_AUTH_EMAIL_PEPPER = "<segredo-aleatorio>"
python scripts/generate_beta_credentials.py
```

O script pede os dois e-mails e as senhas interativamente. As senhas usam
scrypt com salt aleatório, não aparecem no terminal, não são aceitas por
argumento e não são gravadas. Copie os quatro valores gerados diretamente para
as variáveis do Railway. Guarde também o pepper; trocar o pepper invalida os
digests de e-mail.

## Variáveis no Vercel

```text
BACKEND_API_URL=https://<servico>.up.railway.app
BETA_PROXY_SECRET=<segredo-compartilhado-longo>
```

Não crie versões `NEXT_PUBLIC_*` dessas variáveis.

## Variáveis no Railway

```text
BETA_AUTH_ENABLED=true
BETA_AUTH_EMAIL_PEPPER=<mesmo-pepper-usado-no-gerador>
BETA_GROUP_EMAIL_DIGEST=<valor-gerado>
BETA_GROUP_PASSWORD_HASH=<valor-gerado>
BETA_MOTIVA_EMAIL_DIGEST=<valor-gerado>
BETA_MOTIVA_PASSWORD_HASH=<valor-gerado>
BETA_AUTH_TOKEN_SECRET=<segredo-aleatorio-diferente>
BETA_PROXY_SECRET=<mesmo-segredo-configurado-no-Vercel>
DATABASE_URL=sqlite:////workspace/persistent/motiva.db
AUTO_ANALYSIS_DB_PATH=/workspace/persistent/automatic_analysis.sqlite3
API_OUTPUT_ROOT=/workspace/persistent/outputs/satellite_monitoring
AUTO_ANALYSIS_ROADS_DATASET_PATH=/workspace/data/roads/processed/motiva-sp-roads-state.geojson
```

`BETA_AUTH_COOKIE_SECURE` deve permanecer ausente (o padrão é `true`) em
produção. Para desenvolvimento local exclusivamente HTTP, use
`BETA_AUTH_COOKIE_SECURE=false` nos dois processos e configure o mesmo
`BETA_PROXY_SECRET` em ambos.

As sessões expiram em oito horas e ficam no cookie `motiva_beta_session` com
`HttpOnly`, `SameSite=Lax`, `Path=/` e `Secure` em produção. Os únicos scopes
são `group` (Equipe do Projeto) e `motiva` (Equipe Motiva).
