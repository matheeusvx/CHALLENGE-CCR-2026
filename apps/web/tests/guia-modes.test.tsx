import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GuiaWidget } from "@/components/guia/guia-widget";
import { SourcesView } from "@/components/views/sources-view";
import { SettingsView } from "@/components/views/settings-view";
import { AppShell } from "@/components/layout/app-shell";
import {
  useGuiaStore,
  GUIA_DISPLAY_MODE_STORAGE_KEY,
} from "@/stores/guia-store";
import { GUIA_LAYOUT_STORAGE_KEY } from "@/components/guia/use-guia-layout";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

function renderWithClient(ui: React.ReactElement) {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

describe("Sentinel-1 em Fontes de Dados", () => {
  it("exibe Sentinel-1 GRD e Sentinel-2 L2A na lista de fontes de dados", () => {
    render(<SourcesView />);

    // Ambos os nomes de satélite devem estar presentes
    expect(screen.getByText("Sentinel-1 GRD")).toBeInTheDocument();
    expect(screen.getByText("Sentinel-2 L2A")).toBeInTheDocument();

    // Provider do Sentinel-1
    const providers = screen.getAllByText("Microsoft Planetary Computer");
    expect(providers.length).toBeGreaterThanOrEqual(2);

    // Tipo SAR C-band
    expect(screen.getByText("Radar SAR C-band")).toBeInTheDocument();

    // Resolução ~10m
    expect(screen.getByText("aproximadamente 10 m por pixel")).toBeInTheDocument();

    // Frequência ~6 dias
    expect(screen.getByText("Revisita de aproximadamente 6 dias")).toBeInTheDocument();

    // Bandas VV e VH
    expect(screen.getByText("VV")).toBeInTheDocument();
    expect(screen.getByText("VH")).toBeInTheDocument();

    // Uso
    expect(
      screen.getByText(/Retroespalhamento SAR calibrado em sigma0/i)
    ).toBeInTheDocument();
  });

  it("exibe observações discretas para o Sentinel-1", () => {
    render(<SourcesView />);

    expect(
      screen.getByText(/Funciona independentemente de cobertura de nuvens/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Utilizado atualmente em shadow mode/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Não altera diretamente a recomendação operacional/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Polarizações VV\/VH não representam altura ou biomassa/i)
    ).toBeInTheDocument();
  });
});

describe("Personalização da GuIA e Modos de Exibição", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    useGuiaStore.setState({ displayMode: "floating", isOpen: false });
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    window.HTMLElement.prototype.setPointerCapture = vi.fn();
    window.HTMLElement.prototype.releasePointerCapture = vi.fn();
    window.HTMLElement.prototype.hasPointerCapture = vi.fn().mockReturnValue(true);
  });

  it("inicia com modo floating por padrão e salva alterações no localStorage", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente gu\.?ia/i }));

    const panel = container.querySelector(".guia-panel");
    expect(panel).toHaveAttribute("data-display-mode", "floating");
    expect(panel).toHaveClass("guia-panel--floating");

    // Muda para docked
    const dockedBtn = screen.getByRole("radio", { name: /modo painel fixo/i });
    fireEvent.click(dockedBtn);

    expect(panel).toHaveAttribute("data-display-mode", "docked");
    expect(panel).toHaveClass("guia-panel--docked");
    expect(window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY)).toBe("docked");

    // Muda para expanded
    const expandedBtn = screen.getByRole("radio", { name: /modo tela ampla/i });
    fireEvent.click(expandedBtn);

    expect(panel).toHaveAttribute("data-display-mode", "expanded");
    expect(panel).toHaveClass("guia-panel--expanded");
    expect(window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY)).toBe("expanded");

    // Volta para floating
    const floatingBtn = screen.getByRole("radio", { name: /modo flutuante/i });
    fireEvent.click(floatingBtn);

    expect(panel).toHaveAttribute("data-display-mode", "floating");
    expect(window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY)).toBe("floating");
  });

  it("preserva layout existente do floating (motiva-guia-layout-v1) sem apagar", () => {
    const savedLayout = { x: 300, y: 150, width: 500, height: 600 };
    window.localStorage.setItem(GUIA_LAYOUT_STORAGE_KEY, JSON.stringify(savedLayout));

    render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente gu\.?ia/i }));

    // Muda para docked e depois expanded
    fireEvent.click(screen.getByRole("radio", { name: /modo painel fixo/i }));
    fireEvent.click(screen.getByRole("radio", { name: /modo tela ampla/i }));

    // Chave motiva-guia-layout-v1 deve continuar intacta
    const persisted = JSON.parse(window.localStorage.getItem(GUIA_LAYOUT_STORAGE_KEY) ?? "{}");
    expect(persisted.x).toBe(300);
    expect(persisted.y).toBe(150);
    expect(persisted.width).toBe(500);
    expect(persisted.height).toBe(600);
  });

  it("preserva histórico de mensagens e texto do input ao alternar entre os 3 modos", () => {
    render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente gu\.?ia/i }));

    const input = screen.getByPlaceholderText(/digite sua mensagem/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Minha pergunta em andamento" } });

    // Alterna para docked
    fireEvent.click(screen.getByRole("radio", { name: /modo painel fixo/i }));
    expect(input.value).toBe("Minha pergunta em andamento");
    expect(screen.getByText(/Olá! Sou o gu\.?ia/i)).toBeInTheDocument();

    // Alterna para expanded
    fireEvent.click(screen.getByRole("radio", { name: /modo tela ampla/i }));
    expect(input.value).toBe("Minha pergunta em andamento");
    expect(screen.getByText(/Olá! Sou o gu\.?ia/i)).toBeInTheDocument();

    // Volta para floating
    fireEvent.click(screen.getByRole("radio", { name: /modo flutuante/i }));
    expect(input.value).toBe("Minha pergunta em andamento");
    expect(screen.getByText(/Olá! Sou o gu\.?ia/i)).toBeInTheDocument();
  });

  it("esconde handle de resize e botão de reset em docked e expanded, mantendo em floating", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente gu\.?ia/i }));

    // Em floating: reset e resize visíveis
    expect(screen.getByRole("button", { name: /restaurar tamanho e posição/i })).toBeInTheDocument();
    expect(container.querySelector(".guia-resize-handle")).toBeInTheDocument();

    // Alterna para docked: reset e resize não devem existir
    fireEvent.click(screen.getByRole("radio", { name: /modo painel fixo/i }));
    expect(screen.queryByRole("button", { name: /restaurar tamanho e posição/i })).toBeNull();
    expect(container.querySelector(".guia-resize-handle")).toBeNull();

    // Alterna para expanded: reset e resize não devem existir
    fireEvent.click(screen.getByRole("radio", { name: /modo tela ampla/i }));
    expect(screen.queryByRole("button", { name: /restaurar tamanho e posição/i })).toBeNull();
    expect(container.querySelector(".guia-resize-handle")).toBeNull();

    // Retorna para floating: reset e resize reaparecem
    fireEvent.click(screen.getByRole("radio", { name: /modo flutuante/i }));
    expect(screen.getByRole("button", { name: /restaurar tamanho e posição/i })).toBeInTheDocument();
    expect(container.querySelector(".guia-resize-handle")).toBeInTheDocument();
  });

  it("Configurações exibe o card GuIA com as 3 opções e altera o modo reativamente", () => {
    renderWithClient(<SettingsView />);

    expect(screen.getByText("Escolha como o assistente será exibido no painel.")).toBeInTheDocument();

    const optFloating = screen.getByRole("radio", { name: /flutuante/i });
    const optDocked = screen.getByRole("radio", { name: /painel fixo/i });
    const optExpanded = screen.getByRole("radio", { name: /tela ampla/i });

    expect(optFloating).toBeInTheDocument();
    expect(optDocked).toBeInTheDocument();
    expect(optExpanded).toBeInTheDocument();

    // Descrições
    expect(screen.getByText("Janela móvel e redimensionável.")).toBeInTheDocument();
    expect(screen.getByText("Mantém a gu.ia aberta ao lado do conteúdo.")).toBeInTheDocument();
    expect(screen.getByText("Abre o assistente em uma área maior para conversas extensas.")).toBeInTheDocument();

    // Floating ativo por padrão
    expect(optFloating).toHaveAttribute("aria-checked", "true");
    expect(optDocked).toHaveAttribute("aria-checked", "false");

    // Clica em Painel fixo
    fireEvent.click(optDocked);
    expect(useGuiaStore.getState().displayMode).toBe("docked");
    expect(window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY)).toBe("docked");
    expect(optDocked).toHaveAttribute("aria-checked", "true");

    // Clica em Tela ampla
    fireEvent.click(optExpanded);
    expect(useGuiaStore.getState().displayMode).toBe("expanded");
    expect(window.localStorage.getItem(GUIA_DISPLAY_MODE_STORAGE_KEY)).toBe("expanded");
    expect(optExpanded).toHaveAttribute("aria-checked", "true");
  });

  it("AppShell aplica has-docked-guia quando GuIA docked estiver aberta", () => {
    useGuiaStore.setState({ displayMode: "docked", isOpen: false });

    const { container } = renderWithClient(
      <AppShell>
        <div>Workspace Content</div>
      </AppShell>
    );

    const shell = container.querySelector(".app-shell");
    expect(shell).not.toHaveClass("has-docked-guia");

    // Abre GuIA
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente gu\.?ia/i }));
    expect(shell).toHaveClass("has-docked-guia");

    // Fecha GuIA
    const closeBtn = screen.getByRole("button", { name: /fechar chat/i });
    fireEvent.click(closeBtn);
    expect(shell).not.toHaveClass("has-docked-guia");
  });
});
