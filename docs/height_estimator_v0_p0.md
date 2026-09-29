# Height Estimator v0 — P0 estrutural

O estimador é suporte experimental à decisão e não mede altura física em campo.
O modelo e a recommendation operacional são independentes.

## Máscaras

`quality_valid_mask` (`RasterSceneData.valid_mask`) permanece inalterada e
continua atendendo NDVI, série temporal e recommendation. Ela rejeita SCL
`0, 1, 3, 8, 9, 10, 11`.

`height_valid_mask` é exclusiva da evidência de altura. Ela exige:

- interseção real com a AOI e validade radiométrica/quality mask;
- SCL `4` (vegetation);
- NDVI finito e `>= 0.15`.

Para a height mask são rejeitadas as classes SCL
`0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11`. O NDVI mínimo é somente um filtro de
pureza contra pixels claramente não vegetados, nunca proxy de altura. Nas 186
amostras v0, NDVI apresentou mínimo 0,065, Q1 0,277 e mediana 0,340; o limite
permissivo 0,15 afeta a cauda inferior sem representar um threshold operacional.

`vegetation_fraction = height_valid_pixel_count / height_total_pixel_count`,
onde o denominador contém os pixels espacialmente elegíveis dentro da AOI.

O diagnóstico `mixed_pixel_risk` é determinístico:

- `high`: zero pixels espaciais, menos de 3 pixels válidos ou fração < 0,25;
- `medium`: menos de 10 pixels válidos, AOI com menos de 10 pixels ou fração < 0,60;
- `low`: demais casos.

Risco `high`, menos de 3 pixels válidos ou fração < 0,25 produz abstinência
(`inconclusive`). Falha de leitura/feature continua `unavailable`.

## Score e compatibilidade

`score_gt_30_cm` é a nomenclatura canônica. O modelo usa pesos de classe e não
possui calibração probabilística, portanto `calibration_status = uncalibrated`.
`probability_gt_30_cm` permanece temporariamente como alias numérico deprecated
para históricos e clientes v0; nenhum desses campos é mostrado ao operador.

## Coleta futura

O schema reutilizável está em `datasets/field_schema.py`. O alvo futuro proposto,
não aplicado ao dataset histórico, é `height_p90_cm > 30`, com zona cinzenta:

- `height_p90_cm <= 25`: clear negative;
- `25 < height_p90_cm < 35`: uncertainty zone;
- `height_p90_cm >= 35`: clear positive.
