# Web

Frontend Next.js da solução **Motiva Faixa Verde**. A tela principal é um
workspace geoespacial para delimitar visualmente uma faixa lateral rodoviária,
validar a AOI pela API e executar o pipeline real Sentinel-2.

## Execução Local

Na raiz do repositório, em PowerShell:

```powershell
Copy-Item apps\web\.env.example apps\web\.env.local
pnpm install
pnpm --dir apps\web generate:api
pnpm --dir apps\web dev
```

A aplicação abre em `http://localhost:3000`. A API deve estar disponível em
`http://localhost:8000`, salvo quando `NEXT_PUBLIC_API_URL` definir outro
endereço.

## Uso Do Mapa

1. Use os controles do mapa para navegar até o trecho desejado.
2. Selecione **Desenhar polígono**, clique para adicionar os vértices e clique
   no primeiro ponto para concluir.
3. Selecione **Editar vértices** para ajustar o contorno.
4. Confira a área provisória e use **Validar área** para obter os valores
   oficiais da API.
5. Use **Executar análise**. A geometria permanece no mapa durante o
   processamento e no resultado.

O workspace possui somente as abas **Área** e **Resultado**. O operador não
configura datas, filtros Sentinel-2 nem thresholds de recomendação. A API usa o
perfil operacional padrão e uma janela automática de um mês calendário; o
período exato utilizado aparece no resultado.

## Resultado Operacional

A aba **Resultado** prioriza a recomendação, a confiança, a qualidade geral da
análise, a área, o período, as principais justificativas e a evolução temporal
do NDVI. IDs STAC, métricas intermediárias, thresholds, scores por cena e a
tabela técnica de cenas não fazem parte da visão operacional.

Essa simplificação ocorre somente na apresentação. Os dados técnicos continuam
na resposta da API e nos artefatos `summary.json`, `quality_report.json` e CSVs
para auditoria, testes, calibração e futuros relatórios. A recomendação permanece
experimental, não mede diretamente a altura da vegetação e não substitui a
inspeção de campo.

O mapa abre em Louveira (`-46.955`, `-23.121`) no zoom 12 e usa a base
**Operacional**, definida localmente como style raster com tiles do
OpenStreetMap. A opção **Terreno** fica temporariamente indisponível. A camada
da área é separada do editor e permanece visível ao trocar de aba, validar ou
analisar.

O comando de execução volta a ser bloqueado sempre que a geometria é alterada.
Pressione `Esc` sobre o mapa para voltar ao modo de navegação. `Ctrl+Z` desfaz
e `Ctrl+Shift+Z` refaz alterações enquanto o mapa estiver em foco.
Ao concluir um desenho, aplicar um GeoJSON, importar um arquivo, abrir o
resultado ou usar **Enquadrar**, a câmera ajusta a AOI com zoom máximo 16. A
câmera não acompanha continuamente o movimento de cada vértice durante a
edição.

Somente um Polygon fica ativo nesta etapa. O contorno deve representar a faixa
lateral predominantemente gramada, evitando pista, estruturas, água e árvores
densas. Os valores calculados no navegador com Turf são apenas uma prévia; área,
centroide, bounding box e pixels oficiais exibidos depois da validação vêm da
API Python.

## GeoJSON E Upload

A seção **Entrada avançada por GeoJSON** aceita:

- um objeto `Polygon`;
- uma `Feature` com `Polygon`;
- uma `FeatureCollection` com exatamente uma `Feature` Polygon.

As coordenadas devem seguir EPSG:4326 no formato `[longitude, latitude]`. É
possível colar, formatar, copiar e baixar a geometria atual. Uploads `.json` e
`.geojson` são lidos apenas no navegador, limitados a 2 MB, normalizados para
Polygon e enviados ao backend somente como geometria.

## Mapa Base

O style operacional não exige token e fica tipado em `lib/map/config.ts`.
Viewport, limites de zoom e parâmetros de enquadramento também permanecem
centralizados nesse arquivo.

## Arquitetura

- `components/map/`: MapLibre, Terra Draw, seletor de base, controles, legenda e overlays.
- `components/map/layers/`: AOI persistente e IDs reservados para fontes territoriais oficiais.
- `components/workspace/`: composição mapa, abas e painel operacional.
- `components/analysis/`: área, GeoJSON avançado e resultados.
- `lib/map/`: configuração, normalização e cálculos provisórios Turf.
- `lib/api/`: cliente HTTP centralizado e tipos gerados do OpenAPI.
- `stores/`: geometria, revisão, origem, viewport e estado visual compartilhado.

TanStack Query continua responsável por healthcheck, validação, execução,
loading e erros. Zustand guarda somente o rascunho operacional; respostas da
análise permanecem no fluxo da Query. MapLibre e Terra Draw são carregados
somente no cliente e desmontados com todos os listeners.

## Qualidade

```powershell
pnpm --dir apps\web lint
pnpm --dir apps\web test
pnpm --dir apps\web build
```

Os testes usam rede e mapa controlados. Eles não acessam tiles nem Sentinel-2.

## Limitações

- Apenas um Polygon é suportado no editor visual.
- Não há mapa raster Sentinel-2, cadastro de rodovias ou corredor automático.
- Não há ainda overlay próprio de municípios ou rodovias administradas. A base
  cartográfica fornece o contexto viário; nenhuma geometria territorial foi inventada.
- A geometria e o resultado não persistem após recarregar a página.
- O mapa base depende do provedor configurado e de acesso à rede.
- A execução da API ainda é síncrona.
- Existe apenas um perfil operacional interno; perfis por cenário ainda não
  foram implementados e seus thresholds permanecem experimentais.

O próximo módulo cartográfico recomendado é a visualização de cobertura e
qualidade das cenas sobre a AOI, antes da adição de persistência espacial.
