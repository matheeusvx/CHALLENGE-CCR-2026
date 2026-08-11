# Web

Frontend Next.js da Motiva Vegetation Intelligence. A tela principal e um
workspace geoespacial para delimitar visualmente uma faixa lateral rodoviaria,
validar a AOI pela API e executar o pipeline real Sentinel-2.

## Execucao local

Na raiz do repositorio, em PowerShell:

```powershell
Copy-Item apps\web\.env.example apps\web\.env.local
pnpm install
pnpm --dir apps\web generate:api
pnpm --dir apps\web dev
```

A aplicacao abre em `http://localhost:3000`. A API deve estar disponivel em
`http://localhost:8000`, salvo quando `NEXT_PUBLIC_API_URL` definir outro
endereco.

## Uso do mapa

1. Use os controles do mapa para navegar ate o trecho desejado.
2. Selecione **Desenhar poligono**, clique para adicionar os vertices e clique
   no primeiro ponto para concluir.
3. Selecione **Editar vertices** para ajustar o contorno.
4. Confira a area provisoria e use **Validar area** para obter os valores
   oficiais da API.
5. Use **Executar analise**. A geometria permanece no mapa durante o
   processamento e no resultado.

O workspace possui somente as abas **Area** e **Resultado**. O operador nao
configura datas, filtros Sentinel-2 nem thresholds de recomendacao. A API usa o
perfil operacional padrao e uma janela automatica de um mes calendario; o
periodo exato utilizado aparece no resultado.

## Resultado operacional

A aba **Resultado** prioriza a recomendacao, a confianca, a qualidade geral da
analise, a area, o periodo, as principais justificativas e a evolucao temporal
do NDVI. IDs STAC, metricas intermediarias, thresholds, scores por cena e a
tabela tecnica de cenas nao fazem parte da visao operacional.

Essa simplificacao ocorre somente na apresentacao. Os dados tecnicos continuam
na resposta da API e nos artefatos `summary.json`, `quality_report.json` e CSVs
para auditoria, testes, calibracao e futuros relatorios. A recomendacao permanece
experimental, nao mede diretamente a altura da vegetacao e nao substitui a
inspecao de campo.

O mapa abre em Louveira (`-46.955`, `-23.121`) no zoom 12 e usa a base
**Operacional**, definida localmente como style raster com tiles do
OpenStreetMap. A opcao **Terreno** fica temporariamente indisponivel. A camada
da area e separada do editor e permanece visivel ao trocar de aba, validar ou
analisar.

O comando de execucao volta a ser bloqueado sempre que a geometria e alterada.
Pressione `Esc` sobre o mapa para voltar ao modo de navegacao. `Ctrl+Z` desfaz
e `Ctrl+Shift+Z` refaz alteracoes enquanto o mapa estiver em foco.
Ao concluir um desenho, aplicar um GeoJSON, importar um arquivo, abrir o
resultado ou usar **Enquadrar**, a camera ajusta a AOI com zoom maximo 16. A
camera nao acompanha continuamente o movimento de cada vertice durante a
edicao.

Somente um Polygon fica ativo nesta etapa. O contorno deve representar a faixa
lateral predominantemente gramada, evitando pista, estruturas, agua e arvores
densas. Os valores calculados no navegador com Turf sao apenas uma previa; area,
centroide, bounding box e pixels oficiais exibidos depois da validacao vem da
API Python.

## GeoJSON e upload

A secao **Entrada avancada por GeoJSON** aceita:

- um objeto `Polygon`;
- uma `Feature` com `Polygon`;
- uma `FeatureCollection` com exatamente uma `Feature` Polygon.

As coordenadas devem seguir EPSG:4326 no formato `[longitude, latitude]`. E
possivel colar, formatar, copiar e baixar a geometria atual. Uploads `.json` e
`.geojson` sao lidos apenas no navegador, limitados a 2 MB, normalizados para
Polygon e enviados ao backend somente como geometria.

## Mapa base

O style operacional nao exige token e fica tipado em `lib/map/config.ts`.
Viewport, limites de zoom e parametros de enquadramento tambem permanecem
centralizados nesse arquivo.

## Arquitetura

- `components/map/`: MapLibre, Terra Draw, seletor de base, controles, legenda e overlays.
- `components/map/layers/`: AOI persistente e IDs reservados para fontes territoriais oficiais.
- `components/workspace/`: composicao mapa, abas e painel operacional.
- `components/analysis/`: area, GeoJSON avancado e resultados.
- `lib/map/`: configuracao, normalizacao e calculos provisiorios Turf.
- `lib/api/`: cliente HTTP centralizado e tipos gerados do OpenAPI.
- `stores/`: geometria, revisao, origem, viewport e estado visual compartilhado.

TanStack Query continua responsavel por healthcheck, validacao, execucao,
loading e erros. Zustand guarda somente o rascunho operacional; respostas da
analise permanecem no fluxo da Query. MapLibre e Terra Draw sao carregados
somente no cliente e desmontados com todos os listeners.

## Qualidade

```powershell
pnpm --dir apps\web lint
pnpm --dir apps\web test
pnpm --dir apps\web build
```

Os testes usam rede e mapa controlados. Eles nao acessam tiles nem Sentinel-2.

## Limitacoes

- Apenas um Polygon e suportado no editor visual.
- Nao ha mapa raster Sentinel-2, cadastro de rodovias ou corredor automatico.
- Nao ha ainda overlay proprio de municipios ou rodovias administradas. A base
  vetorial fornece o contexto viario; nenhuma geometria territorial foi inventada.
- A geometria e o resultado nao persistem apos recarregar a pagina.
- O mapa base depende do provedor configurado e de acesso a rede.
- A execucao da API ainda e sincrona.
- Existe apenas um perfil operacional interno; perfis por cenario ainda nao
  foram implementados e seus thresholds permanecem experimentais.

O proximo modulo cartografico recomendado e a visualizacao de cobertura e
qualidade das cenas sobre a AOI, antes da adicao de persistencia espacial.
