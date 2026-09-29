"use client";

import { useEffect, useRef, useState } from "react";
import { AppWindow, Maximize2, PanelRight, RotateCcw, Sparkles } from "lucide-react";
import { toApiUrl } from "@/lib/api/client";
import { useGuiaStore } from "@/stores/guia-store";
import { FormattedMessage } from "./guia-message-formatter";
import { useGuiaLayout } from "./use-guia-layout";

type ChatMessage = {
  autor: "guia" | "operador";
  texto: string;
};

const MENSAGEM_INICIAL: ChatMessage = {
  autor: "guia",
  texto:
    "Olá! Sou o gu.ia, seu parceiro aqui na plataforma CCR 2026. Como posso te ajudar?",
};

export function GuiaWidget() {
  const displayMode = useGuiaStore((state) => state.displayMode);
  const setDisplayMode = useGuiaStore((state) => state.setDisplayMode);
  const aberto = useGuiaStore((state) => state.isOpen);
  const setAberto = useGuiaStore((state) => state.setIsOpen);
  const toggleAberto = useGuiaStore((state) => state.toggleOpen);

  const [mensagens, setMensagens] = useState<ChatMessage[]>([MENSAGEM_INICIAL]);
  const [entrada, setEntrada] = useState("");
  const [carregando, setCarregando] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const fimRef = useRef<HTMLDivElement | null>(null);

  const {
    layout,
    isDragging,
    isResizing,
    resetLayout,
    headerDragProps,
    resizeHandleProps,
  } = useGuiaLayout();

  useEffect(() => {
    return () => {
      setAberto(false);
    };
  }, [setAberto]);

  useEffect(() => {
    fimRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensagens, aberto]);

  useEffect(() => {
    if (!aberto) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAberto(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [aberto, setAberto]);

  const enviar = async (evento: React.FormEvent) => {
    evento.preventDefault();
    const texto = entrada.trim();
    if (!texto || carregando) return;

    setMensagens((atual) => [...atual, { autor: "operador", texto }]);
    setEntrada("");
    setCarregando(true);

    try {
      const resposta = await fetch(toApiUrl("/api/guia/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mensagem: texto,
          session_id: sessionIdRef.current,
        }),
      });

      const dados = (await resposta.json().catch(() => null)) as
        | { resposta?: string; session_id?: string; detail?: string }
        | null;

      if (resposta.ok && dados?.resposta) {
        if (dados.session_id) sessionIdRef.current = dados.session_id;
        setMensagens((atual) => [
          ...atual,
          { autor: "guia", texto: dados.resposta as string },
        ]);
      } else {
        setMensagens((atual) => [
          ...atual,
          {
            autor: "guia",
            texto:
              "Desculpe, não consegui responder agora. Tente de novo em instantes.",
          },
        ]);
      }
    } catch {
      setMensagens((atual) => [
        ...atual,
        {
          autor: "guia",
          texto: "Não consegui me conectar. Verifique sua conexão e tente de novo.",
        },
      ]);
    } finally {
      setCarregando(false);
    }
  };

  return (
    <div className="sidebar-guia">
      <button
        type="button"
        className={`guia-sidebar-btn ${aberto ? "active" : ""}`}
        aria-label={aberto ? "Fechar assistente gu.ia" : "Abrir assistente gu.ia"}
        aria-expanded={aberto}
        aria-haspopup="dialog"
        data-tour="guia-widget"
        onClick={() => toggleAberto()}
      >
        <span className="guia-sidebar-icon" aria-hidden="true">
          <Sparkles size={16} />
        </span>
        <span className="guia-sidebar-copy">
          <strong>gu.ia</strong>
          <small>Assistente virtual</small>
        </span>
        <span className="sidebar-tooltip" role="tooltip">gu.ia — Assistente virtual</span>
        {aberto ? (
          <span className="guia-sidebar-indicator" aria-hidden="true" />
        ) : null}
      </button>

      {aberto ? (
        <div
          className={`guia-panel guia-panel--${displayMode} ${isDragging ? "is-dragging" : ""} ${isResizing ? "is-resizing" : ""}`}
          role="dialog"
          aria-label="Assistente gu.ia"
          data-display-mode={displayMode}
          style={
            displayMode === "floating"
              ? {
                  left: `${layout.x}px`,
                  top: `${layout.y}px`,
                  width: `${layout.width}px`,
                  height: `${layout.height}px`,
                }
              : undefined
          }
        >
          <header
            className="guia-panel-header"
            {...(displayMode === "floating" ? headerDragProps : {})}
            title={displayMode === "floating" ? "Arraste para mover a janela" : undefined}
          >
            <div className="guia-avatar" aria-hidden="true">
              <Sparkles size={18} />
            </div>
            <div className="guia-header-info">
              <strong>gu.ia</strong>
              <span>Assistente CCR 2026</span>
            </div>

            <div
              className="guia-mode-switcher"
              role="radiogroup"
              aria-label="Modo de exibição da gu.ia"
              data-no-drag="true"
            >
              <button
                type="button"
                role="radio"
                aria-checked={displayMode === "floating"}
                className={`guia-mode-btn ${displayMode === "floating" ? "active" : ""}`}
                aria-label="Modo flutuante"
                title="Modo flutuante"
                data-no-drag="true"
                onClick={() => setDisplayMode("floating")}
              >
                <AppWindow size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={displayMode === "docked"}
                className={`guia-mode-btn ${displayMode === "docked" ? "active" : ""}`}
                aria-label="Modo painel fixo"
                title="Modo painel fixo"
                data-no-drag="true"
                onClick={() => setDisplayMode("docked")}
              >
                <PanelRight size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={displayMode === "expanded"}
                className={`guia-mode-btn ${displayMode === "expanded" ? "active" : ""}`}
                aria-label="Modo tela ampla"
                title="Modo tela ampla"
                data-no-drag="true"
                onClick={() => setDisplayMode("expanded")}
              >
                <Maximize2 size={14} aria-hidden="true" />
              </button>
            </div>

            <div className="guia-header-actions">
              {displayMode === "floating" ? (
                <button
                  type="button"
                  className="guia-header-action guia-reset"
                  aria-label="Restaurar tamanho e posição"
                  title="Restaurar tamanho e posição"
                  data-no-drag="true"
                  onClick={resetLayout}
                >
                  <RotateCcw size={15} aria-hidden="true" />
                </button>
              ) : null}
              <button
                type="button"
                className="guia-header-action guia-close"
                aria-label="Fechar chat"
                title="Fechar chat"
                data-no-drag="true"
                onClick={() => setAberto(false)}
              >
                ×
              </button>
            </div>
          </header>

          <div className="guia-messages">
            {mensagens.map((m, i) => (
              <div key={i} className={`guia-message ${m.autor}`}>
                <div className="guia-bubble">
                  <FormattedMessage text={m.texto} />
                </div>
              </div>
            ))}
            {carregando ? (
              <div className="guia-message guia">
                <div className="guia-bubble guia-typing">
                  gu.ia está digitando...
                </div>
              </div>
            ) : null}
            <div ref={fimRef} />
          </div>

          <form className="guia-input" onSubmit={enviar}>
            <input
              type="text"
              value={entrada}
              placeholder="Digite sua mensagem..."
              onChange={(e) => setEntrada(e.target.value)}
              disabled={carregando}
              aria-label="Mensagem para o gu.ia"
            />
            <button type="submit" disabled={carregando || !entrada.trim()}>
              Enviar
            </button>
          </form>

          {/* Handle de redimensionamento apenas no modo flutuante */}
          {displayMode === "floating" ? (
            <div
              className="guia-resize-handle"
              {...resizeHandleProps}
              title="Redimensionar janela"
              aria-hidden="true"
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
                <path
                  d="M9 1L1 9M9 5L5 9M9 9H9.01"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
