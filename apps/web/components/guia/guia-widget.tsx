"use client";

import { useEffect, useRef, useState } from "react";
import { toApiUrl } from "@/lib/api/client";

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

  useEffect(() => {
    fimRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensagens, aberto]);

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
    <div className="guia-widget">
      {aberto ? (
        <div className="guia-panel" role="dialog" aria-label="Assistente Guia">
          <header className="guia-panel-header">
            <div className="guia-avatar" aria-hidden="true">
              G
            </div>
            <div className="guia-header-info">
              <strong>Guia</strong>
              <span>Assistente CCR 2026</span>
            </div>
            <button
              type="button"
              className="guia-close"
              aria-label="Fechar chat"
              onClick={() => setAberto(false)}
            >
              ×
            </button>
          </header>

          <div className="guia-messages">
            {mensagens.map((m, i) => (
              <div key={i} className={`guia-message ${m.autor}`}>
                <div className="guia-bubble">{m.texto}</div>
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
        </div>
      ) : null}

      <button
        type="button"
        className="guia-fab"
        aria-label={aberto ? "Fechar assistente Guia" : "Abrir assistente Guia"}
        onClick={() => setAberto((v) => !v)}
      >
        {aberto ? "×" : "Guia"}
      </button>
    </div>
  );
}
