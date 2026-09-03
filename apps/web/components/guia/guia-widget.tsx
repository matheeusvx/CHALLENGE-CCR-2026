"use client";

import { useEffect, useRef, useState } from "react";
import { RotateCcw, Sparkles } from "lucide-react";
import { toApiUrl } from "@/lib/api/client";
import { FormattedMessage } from "./guia-message-formatter";
import { useGuiaLayout } from "./use-guia-layout";

type ChatMessage = {
  autor: "guia" | "operador";
  texto: string;
};

const MENSAGEM_INICIAL: ChatMessage = {
  autor: "guia",
  texto:
    "Olá! Sou o Guia, seu parceiro aqui na plataforma CCR 2026. Como posso te ajudar?",
};

export function GuiaWidget() {
  const [aberto, setAberto] = useState(false);
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
    fimRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensagens, aberto]);

  useEffect(() => {
    if (!aberto) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAberto(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [aberto]);

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
        aria-label={aberto ? "Fechar assistente GuIA" : "Abrir assistente GuIA"}
        aria-expanded={aberto}
        aria-haspopup="dialog"
        onClick={() => setAberto((v) => !v)}
      >
        <span className="guia-sidebar-icon" aria-hidden="true">
          <Sparkles size={17} />
        </span>
        <span className="guia-sidebar-copy">
          <strong>GuIA</strong>
          <small>Assistente virtual</small>
        </span>
        {aberto ? (
          <span className="guia-sidebar-indicator" aria-hidden="true" />
        ) : null}
      </button>

      {aberto ? (
        <div
          className={`guia-panel ${isDragging ? "is-dragging" : ""} ${isResizing ? "is-resizing" : ""}`}
          role="dialog"
          aria-label="Assistente GuIA"
          style={{
            left: `${layout.x}px`,
            top: `${layout.y}px`,
            width: `${layout.width}px`,
            height: `${layout.height}px`,
          }}
        >
          <header
            className="guia-panel-header"
            {...headerDragProps}
            title="Arraste para mover a janela"
          >
            <div className="guia-avatar" aria-hidden="true">
              <Sparkles size={18} />
            </div>
            <div className="guia-header-info">
              <strong>GuIA</strong>
              <span>Assistente CCR 2026</span>
            </div>
            <div className="guia-header-actions">
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
                  Guia está digitando...
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
              aria-label="Mensagem para o Guia"
            />
            <button type="submit" disabled={carregando || !entrada.trim()}>
              Enviar
            </button>
          </form>

          {/* Handle de redimensionamento */}
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
        </div>
      ) : null}
    </div>
  );
}
