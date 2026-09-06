/**
 * Configuração estática de apresentação do front-end.
 * Não representa dados do backend — serve para popular as telas de
 * Fontes de dados, Configurações e o rodapé/versão do aplicativo.
 */

export const APP_NAME = "Motiva Faixa Verde";
export const APP_VERSION = "0.1.0";
export const APP_ENVIRONMENT = "Análise operacional";

export type MockUser = {
  name: string;
  email: string;
  username: string;
  role: string;
  organization: string;
};

/** Perfil de operador mockado (substituir por autenticação real futuramente). */
export const mockUser: MockUser = {
  name: "Rafael Ferreira",
  email: "rafael.ferreira@motiva.com.br",
  username: "rafael.ferreira",
  role: "Analista de operações rodoviárias",
  organization: "CCR Motiva",
};

export type DataSourceStatus = "active" | "planned";

export type DataSource = {
  id: string;
  name: string;
  provider: string;
  kind: string;
  resolution: string;
  revisit: string;
  bands: string[];
  usage: string;
  status: DataSourceStatus;
  notes?: string[];
};

/**
 * Fontes de dados exibidas na aba "Fontes de dados". Nova entrada adicionada
 * aqui aparece automaticamente na tela.
 */
export const dataSources: DataSource[] = [
  {
    id: "sentinel-2-l2a",
    name: "Sentinel-2 L2A",
    provider: "Microsoft Planetary Computer",
    kind: "Satélite óptico multiespectral",
    resolution: "10 m por pixel",
    revisit: "Revisita de ~5 dias",
    bands: ["RED", "NIR", "SCL"],
    usage: "Cálculo do NDVI e máscara de qualidade (SCL) restritos à área delimitada pelo operador.",
    status: "active",
  },
  {
    id: "sentinel-1-grd",
    name: "Sentinel-1 GRD",
    provider: "Microsoft Planetary Computer",
    kind: "Radar SAR C-band",
    resolution: "aproximadamente 10 m por pixel",
    revisit: "Revisita de aproximadamente 6 dias",
    bands: ["VV", "VH"],
    usage: "Retroespalhamento SAR calibrado em sigma0 para análise complementar da estrutura radiométrica e evolução temporal da área.",
    status: "active",
    notes: [
      "Funciona independentemente de cobertura de nuvens.",
      "Utilizado atualmente em shadow mode.",
      "Não altera diretamente a recomendação operacional.",
      "Polarizações VV/VH não representam altura ou biomassa diretamente.",
    ],
  },
];
