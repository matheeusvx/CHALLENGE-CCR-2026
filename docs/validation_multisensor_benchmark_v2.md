# Benchmark Multissensor V2

O V2 é um benchmark offline e experimental. Ele mede hipóteses conservadoras de
revisão Sentinel-2 com evidência Sentinel-1, mas não participa do runtime, não
altera recomendações e não cria uma nova decisão.

## Entradas e unidade científica

A unidade científica é uma `validation_sample`/AOI. O builder faz joins por
`sample_id` entre snapshots imutáveis e o benchmark temporal, e por
`aoi_id=sample_id` com o soak. Repetições do soak recebem `scientific_weight=0`:
elas medem apenas reprodutibilidade técnica. Ground truth `uncertain` permanece
nas tabelas descritivas, mas é excluído de métricas supervisionadas.

O período temporal científico é o período histórico do snapshot ou o fallback
baseado em `reference_date` usado pelo benchmark temporal V1. Períodos do soak
são explicitamente marcados como não comparáveis ao período científico.

## Geração

Reconstrução totalmente offline, recomendada após congelar o temporal:

```powershell
.\.venv\Scripts\python.exe scripts/run_validation_multisensor_benchmark_v2.py `
  --validation-db data/validation/validation.sqlite3 `
  --temporal-benchmark-json outputs/validation-v2/validation_temporal_benchmark_input.json `
  --soak-runs outputs/sentinel1-soak/validation-2026-08-r3/sentinel1_soak_runs.json `
  --output-root outputs/validation-v2
```

Para atualizar S1 temporal uma única vez no Microsoft Planetary Computer e
congelar o resultado junto ao artifact, substitua
`--temporal-benchmark-json ...` por `--refresh-temporal`. A consulta recebe
somente geometria, período e parâmetros Sentinel-1; metadados de validação não
são enviados pelo provider.

O arquivo canônico é `validation_multisensor_benchmark_v2.json`. A gravação é
atômica. O endpoint `GET /api/validation-multisensor-benchmark-v2` apenas lê esse
arquivo do caminho `VALIDATION_MULTISENSOR_BENCHMARK_V2_PATH`; ele nunca consulta
STAC ou recalcula o benchmark.

## Contrato JSON 2.0

O artifact contém `schema_version: "2.0"`, data de geração e SHA-256 das três
entradas. As seções estáveis são:

- `dataset` e `input_versions`: cardinalidades, partições e versões;
- `s2_baseline`: matriz TP/FN/FP/TN, proporções e incerteza;
- `sample_matrix`: uma linha sanitizada por AOI, sem geometria ou notes;
- `candidate_rules`: A, B e C isoladas;
- `combinations`: uniões A+B e A+B+C, sem dupla contagem;
- `complementary_evidence`: D, sem correctness ou nova decisão;
- `uncertainty`: Wilson 95%, bootstrap estratificado e seed;
- `known_s2_errors` e `false_reviews`: auditorias predefinidas;
- `soak_reproducibility`: clusters, completude e concordância;
- `recommendation_gate`, `warnings` e `methodology`.

Cada proporção possui `value`, `numerator`, `denominator` e `wilson_95`.
Denominador zero produz `value: null`. Balanced accuracy contém intervalo de
bootstrap estratificado com 10.000 reamostragens e seed `20260907`.

## Interpretação e gates

O evento positivo das regras de review é “erro do Sentinel-2”. A análise
primária é intention-to-review: S1 indisponível conta como não disparo. A análise
secundária `s1_available_only` é per-protocol e vem identificada como tal.

Os estados possíveis são `NO_REVIEW_MODE_YET`, `SHADOW_REVIEW_CANDIDATE` e
`REVIEW_MODE_CANDIDATE`. O último requer holdout independente e preregistrado,
pelo menos 50 truths binários, 10 erros S2 e limites Wilson predefinidos. Logo, o
dataset atual só pode chegar a shadow. Nenhuma regra que passe é escolhida
automaticamente como “melhor”.

## Limitações

O JSON registra explicitamente seleção e avaliação no mesmo dataset, apenas dois
erros S2, desbalanceamento, possível correlação espacial, períodos S1 distintos,
múltiplas comparações, instabilidade leave-one-out, sensibilidade temporal ao
período e risco de pseudorreplicação. Status S1 não deve ser interpretado como
crescimento, biomassa, altura ou necessidade de corte.
