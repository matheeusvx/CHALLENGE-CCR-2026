import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GuiaWidget } from "@/components/guia/guia-widget";
import { AppSidebar } from "@/components/layout/app-sidebar";

describe("GuIA Sidebar Integration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it("não renderiza mais o botão flutuante .guia-fab", () => {
    const { container } = render(<GuiaWidget />);
    expect(container.querySelector(".guia-fab")).toBeNull();
  });

  it("renderiza o item do GuIA com título, subtítulo e ícone", () => {
    render(<GuiaWidget />);
    const button = screen.getByRole("button", { name: /abrir assistente guia/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveTextContent("GuIA");
    expect(button).toHaveTextContent("Assistente virtual");
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("o painel de chat inicia fechado", () => {
    const { container } = render(<GuiaWidget />);
    expect(container.querySelector(".guia-panel")).toBeNull();
  });

  it("abre o painel de chat ao clicar no item GuIA da sidebar", () => {
    const { container } = render(<GuiaWidget />);
    const button = screen.getByRole("button", { name: /abrir assistente guia/i });

    fireEvent.click(button);

    expect(container.querySelector(".guia-panel")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveClass("active");
  });

  it("fecha o painel ao clicar novamente no item GuIA da sidebar", () => {
    const { container } = render(<GuiaWidget />);
    const button = screen.getByRole("button", { name: /abrir assistente guia/i });

    // Abre
    fireEvent.click(button);
    expect(container.querySelector(".guia-panel")).toBeInTheDocument();

    // Fecha ao clicar novamente
    fireEvent.click(button);
    expect(container.querySelector(".guia-panel")).toBeNull();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).not.toHaveClass("active");
  });

  it("fecha o painel ao clicar no botão '×'", () => {
    const { container } = render(<GuiaWidget />);
    const openBtn = screen.getByRole("button", { name: /abrir assistente guia/i });
    fireEvent.click(openBtn);

    const closeBtn = screen.getByRole("button", { name: /fechar chat/i });
    fireEvent.click(closeBtn);

    expect(container.querySelector(".guia-panel")).toBeNull();
  });

  it("fecha o painel ao pressionar Escape", () => {
    const { container } = render(<GuiaWidget />);
    const openBtn = screen.getByRole("button", { name: /abrir assistente guia/i });
    fireEvent.click(openBtn);

    expect(container.querySelector(".guia-panel")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector(".guia-panel")).toBeNull();
  });

  it("mantém a mensagem inicial e envia nova mensagem com sucesso", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        resposta: "Resposta simulada do GuIA",
        session_id: "test-session-123",
      }),
    } as unknown as Response);

    render(<GuiaWidget />);
    fireEvent.click(screen.getByRole("button", { name: /abrir assistente guia/i }));

    expect(
      screen.getByText(/Olá! Sou o Guia, seu parceiro aqui na plataforma CCR 2026/i)
    ).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/digite sua mensagem/i);
    const submitBtn = screen.getByRole("button", { name: /enviar/i });

    fireEvent.change(input, { target: { value: "Como funciona a análise?" } });
    fireEvent.click(submitBtn);

    expect(screen.getByText("Como funciona a análise?")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Resposta simulada do GuIA")).toBeInTheDocument();
    });
  });

  it("AppSidebar posiciona o GuIA entre os itens de navegação e o sidebar-foot", () => {
    const { container } = render(
      <AppSidebar activeView="analysis" onNavigate={vi.fn()} />
    );

    const aside = container.querySelector(".sidebar");
    expect(aside).toBeInTheDocument();

    const nav = aside?.querySelector("nav");
    const guiaArea = aside?.querySelector(".sidebar-guia");
    const foot = aside?.querySelector(".sidebar-foot");

    expect(nav).toBeInTheDocument();
    expect(guiaArea).toBeInTheDocument();
    expect(foot).toBeInTheDocument();

    // Verifica ordem no DOM: nav -> guiaArea -> foot
    const children = Array.from(aside?.children ?? []);
    const navIndex = children.indexOf(nav!);
    const guiaIndex = children.indexOf(guiaArea!);
    const footIndex = children.indexOf(foot!);

    expect(navIndex).toBeLessThan(guiaIndex);
    expect(guiaIndex).toBeLessThan(footIndex);
  });
});
