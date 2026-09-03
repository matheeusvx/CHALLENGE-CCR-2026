import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GuiaWidget } from "@/components/guia/guia-widget";
import { AppSidebar } from "@/components/layout/app-sidebar";
import {
  GUIA_LAYOUT_STORAGE_KEY,
  MIN_WIDTH,
  MIN_HEIGHT,
  clampLayout,
} from "@/components/guia/use-guia-layout";
import { normalizeMessageText } from "@/components/guia/guia-message-formatter";

describe("GuIA Floating & Resizable Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    window.HTMLElement.prototype.setPointerCapture = vi.fn();
    window.HTMLElement.prototype.releasePointerCapture = vi.fn();
    window.HTMLElement.prototype.hasPointerCapture = vi.fn().mockReturnValue(true);
  });

  // 1. FAB removido
  it("não renderiza mais o botão flutuante .guia-fab", () => {
    const { container } = render(<GuiaWidget />);
    expect(container.querySelector(".guia-fab")).toBeNull();
  });

  // 2. Botão lateral
  it("renderiza o item do GuIA com título, subtítulo e ícone", () => {
    render(<GuiaWidget />);
    const button = screen.getByRole("button", { name: /abrir assistente guia/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveTextContent("GuIA");
    expect(button).toHaveTextContent("Assistente virtual");
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  // 3. Abertura e fechamento
  it("abre o painel de chat ao clicar no item GuIA da sidebar e fecha ao clicar novamente", () => {
    const { container } = render(<GuiaWidget />);
    const button = screen.getByRole("button", { name: /abrir assistente guia/i });

    expect(container.querySelector(".guia-panel")).toBeNull();

    // Abre
    fireEvent.click(button);
    expect(container.querySelector(".guia-panel")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveClass("active");

    // Fecha ao clicar de novo
    fireEvent.click(button);
    expect(container.querySelector(".guia-panel")).toBeNull();
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("fecha o painel ao clicar no botão '×'", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const closeBtn = screen.getByRole("button", { name: /fechar chat/i });
    fireEvent.click(closeBtn);

    expect(container.querySelector(".guia-panel")).toBeNull();
  });

  it("fecha o painel ao pressionar Escape", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    expect(container.querySelector(".guia-panel")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector(".guia-panel")).toBeNull();
  });

  // 4. Avatar sem "G" e com Sparkles
  it("avatar no header não contém a letra 'G' e possui ícone svg de Sparkles", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const avatar = container.querySelector(".guia-avatar");
    expect(avatar).toBeInTheDocument();
    expect(avatar?.textContent?.trim()).toBe(""); // não possui texto "G"
    expect(avatar?.querySelector("svg")).toBeInTheDocument();
  });

  // 5. Drag e clamping
  it("permite arrastar o painel pelo header e respeita os limites da viewport", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const panel = container.querySelector(".guia-panel") as HTMLElement;
    const header = container.querySelector(".guia-panel-header") as HTMLElement;
    expect(panel).toBeInTheDocument();

    // Simula PointerDown no header
    fireEvent.pointerDown(header, {
      button: 0,
      clientX: 250,
      clientY: 100,
      pointerId: 1,
    });

    // Simula PointerMove (arrasta 100px para direita e 50px para baixo)
    fireEvent.pointerMove(header, {
      clientX: 350,
      clientY: 150,
      pointerId: 1,
    });

    // Simula PointerUp
    fireEvent.pointerUp(header, {
      pointerId: 1,
    });

    // Verifica se a posição foi persistida no localStorage
    const saved = JSON.parse(window.localStorage.getItem(GUIA_LAYOUT_STORAGE_KEY) ?? "{}");
    expect(typeof saved.x).toBe("number");
    expect(typeof saved.y).toBe("number");
  });

  it("função clampLayout impede que a janela desapareça fora da tela", () => {
    // Tenta colocar X negativo e Y acima da tela
    const offTopLeft = clampLayout({ x: -200, y: -500, width: 440, height: 580 }, 1440, 900);
    expect(offTopLeft.x).toBeGreaterThanOrEqual(8);
    expect(offTopLeft.y).toBeGreaterThanOrEqual(8);

    // Tenta colocar X e Y muito além da tela
    const offBottomRight = clampLayout({ x: 9999, y: 9999, width: 440, height: 580 }, 1440, 900);
    expect(offBottomRight.x).toBeLessThanOrEqual(1440 - 440);
    expect(offBottomRight.y).toBeLessThanOrEqual(900 - 60); // header visível
  });

  // 6. Resize e limites mínimos
  it("permite redimensionar a janela e respeita min-width e min-height", () => {
    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const resizeHandle = container.querySelector(".guia-resize-handle") as HTMLElement;
    expect(resizeHandle).toBeInTheDocument();

    // Simula início de resize
    fireEvent.pointerDown(resizeHandle, {
      button: 0,
      clientX: 600,
      clientY: 500,
      pointerId: 2,
    });

    // Tenta encolher abaixo do limite mínimo
    fireEvent.pointerMove(resizeHandle, {
      clientX: 0,
      clientY: 0,
      pointerId: 2,
    });

    fireEvent.pointerUp(resizeHandle, { pointerId: 2 });

    const saved = JSON.parse(window.localStorage.getItem(GUIA_LAYOUT_STORAGE_KEY) ?? "{}");
    expect(saved.width).toBeGreaterThanOrEqual(MIN_WIDTH);
    expect(saved.height).toBeGreaterThanOrEqual(MIN_HEIGHT);
  });

  // 7. Persistência e restauração
  it("restaura layout salvo do localStorage ao abrir", () => {
    window.localStorage.setItem(
      GUIA_LAYOUT_STORAGE_KEY,
      JSON.stringify({ x: 400, y: 200, width: 500, height: 650 })
    );

    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const panel = container.querySelector(".guia-panel") as HTMLElement;
    expect(panel.style.left).toBe("400px");
    expect(panel.style.top).toBe("200px");
    expect(panel.style.width).toBe("500px");
    expect(panel.style.height).toBe("650px");
  });

  it("botão de restaurar volta o layout para o padrão", () => {
    window.localStorage.setItem(
      GUIA_LAYOUT_STORAGE_KEY,
      JSON.stringify({ x: 500, y: 300, width: 600, height: 700 })
    );

    render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const resetBtn = screen.getByRole("button", { name: /restaurar tamanho e posição/i });
    expect(resetBtn).toBeInTheDocument();

    fireEvent.click(resetBtn);

    // Deve ter removido layout customizado
    expect(window.localStorage.getItem(GUIA_LAYOUT_STORAGE_KEY)).toBeNull();
  });

  // 8. Mensagens e formatação
  it("normaliza escapes de texto como \\n, \\\" e \\\\ corretamente", () => {
    const raw = 'Primeiro passo:\\n- Clique em \\"Nova análise\\"\\n- Depois desenhe a área';
    const normalized = normalizeMessageText(raw);

    expect(normalized).not.toContain("\\n");
    expect(normalized).not.toContain('\\"');
    expect(normalized).toContain('Clique em "Nova análise"');
    expect(normalized.split("\n").length).toBe(3);
  });

  it("renderiza mensagens longas com formatação rica (negrito, listas)", async () => {
    const respostaLonga =
      "Aqui está o resumo:\\n- Ponto 1 com **destaque**\\n- Ponto 2 com `código`\\n\\nConclusão da análise!";

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        resposta: respostaLonga,
        session_id: "test-sess-99",
      }),
    } as unknown as Response);

    const { container } = render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    const input = screen.getByPlaceholderText(/digite sua mensagem/i);
    const submitBtn = screen.getByRole("button", { name: /enviar/i });

    fireEvent.change(input, { target: { value: "Explicar análise" } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText("Ponto 1 com")).toBeInTheDocument();
      expect(screen.getByText("destaque")).toBeInTheDocument();
      expect(container.querySelector("strong")).toBeInTheDocument();
      expect(container.querySelector("ul.guia-bullet-list")).toBeInTheDocument();
      expect(container.querySelector("code")).toBeInTheDocument();
    });
  });

  // 9. Estrutura na sidebar
  it("AppSidebar posiciona o GuIA entre os itens de navegação e o sidebar-foot", () => {
    const { container } = render(
      <AppSidebar activeView="analysis" onNavigate={vi.fn()} />
    );

    const aside = container.querySelector(".sidebar");
    const nav = aside?.querySelector("nav");
    const guiaArea = aside?.querySelector(".sidebar-guia");
    const foot = aside?.querySelector(".sidebar-foot");

    expect(nav).toBeInTheDocument();
    expect(guiaArea).toBeInTheDocument();
    expect(foot).toBeInTheDocument();

    const children = Array.from(aside?.children ?? []);
    expect(children.indexOf(nav!)).toBeLessThan(children.indexOf(guiaArea!));
    expect(children.indexOf(guiaArea!)).toBeLessThan(children.indexOf(foot!));
  });
});
