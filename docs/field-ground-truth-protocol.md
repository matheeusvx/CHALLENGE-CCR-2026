# Protocolo de ground truth de altura

Use este protocolo para registrar a área de vegetação realmente medida, sem transformar observação visual em altura física.

1. Desenhe uma AOI Polygon ou MultiPolygon em EPSG:4326 cobrindo a mesma vegetação que será medida. Não use automaticamente um ponto ou buffer da rodovia.
2. Registre data e hora da coleta, rodovia, KM, lado e identificação do local.
3. Distribua várias medidas físicas pela AOI. Evite medir somente o ponto mais alto e registre o espaçamento aproximado entre medidas.
4. Use `measurement_type=multiple` e informe as medidas individuais em `height_measurements_cm`. O pipeline calculará quantidade, mínimo, média, p50, p90 e máximo pelo método linear determinístico.
5. Se houver apenas uma medida exata, use `measurement_type=exact_single`, `measured_height_cm` e `confirmed_single_measurement`. P50 e p90 não serão inventados.
6. Registre cobertura e tipo de vegetação. Vegetação arbustiva ou arbórea deve ser descrita separadamente da vegetação herbácea.
7. Salve referências das fotos e vídeos e anote sombra, solo, umidade e possível roçada recente, incluindo a data quando conhecida.
8. Use `visual_only` quando não houver medição física. Nunca estime centímetros apenas pela imagem.
9. Exporte a AOI como GeoJSON e confira visualmente se ela não inclui asfalto, solo ou vegetação externa à medição.

Quando a verificação física confirmar somente que a altura supera um limiar, use `measurement_type=threshold_lower_bound`, registre `height_lower_bound_cm` e marque `confirmed_above_lower_bound=true`. Mantenha `measured_height_cm`, média, p50 e p90 nulos. Um lower bound confirmado de pelo menos 35 cm é ground truth forte para classificação `>30`, mas permanece `metric_regression_eligible=false`.

Um bbox pode ser preservado em `reference_bbox` somente como referência espacial. Ele não substitui a AOI de campo: enquanto o Polygon/MultiPolygon real não existir, `geometry` permanece nulo e o estado é `WAITING_FOR_FIELD_AOI_GEOJSON`.

Toda coleta nova entra como holdout (`training_eligible=false`, `external_validation=true`). Promoção futura para treinamento exige decisão explícita e revisão humana.
