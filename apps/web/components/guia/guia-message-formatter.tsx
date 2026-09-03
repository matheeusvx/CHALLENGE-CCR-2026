import React, { type ReactNode } from "react";

/**
 * Normaliza artefatos óbvios de serialização/escape em strings recebidas da API,
 * sem remover aspas legítimas nem alterar o backend.
 */
export function normalizeMessageText(raw: string): string {
  if (!raw) return "";

  let text = raw;

  // Desescapa quebras de linha literais (\r\n, \n, \r)
  text = text.replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\\r/g, "\n");

  // Desescapa aspas duplas e simples com escape (\", \')
  text = text.replace(/\\"/g, '"').replace(/\\'/g, "'");

  // Desescapa tabulações
  text = text.replace(/\\t/g, "\t");

  // Desescapa barras invertidas duplas que sobram
  text = text.replace(/\\\\/g, "\\");

  return text.trim();
}

/**
 * Renderiza texto em linha com suporte seguro a **negrito** e `código`.
 * 100% seguro contra injeção: retorna apenas elementos React puros (sem innerHTML).
 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  // Regex para capturar **negrito** e `código`
  const tokenRegex = /(\*\*[^*]+?\*\*|`[^`]+?`)/g;
  const parts = text.split(tokenRegex);

  return parts
    .filter((part) => part.length > 0)
    .map((part, index) => {
      const key = `${keyPrefix}-${index}`;

      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }

      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return <code key={key}>{part.slice(1, -1)}</code>;
      }

      return <React.Fragment key={key}>{part}</React.Fragment>;
    });
}

/**
 * Componente que formata a mensagem do chat de forma rica, elegante e segura:
 * - Suporta parágrafos
 * - Suporta listas com marcadores (- ou *)
 * - Suporta listas numeradas (1. , 2. )
 * - Suporta negrito (**texto**) e código em linha (`código`)
 * - Sem HTML arbitrário e sem dangerouslySetInnerHTML.
 */
export function FormattedMessage({ text }: { text: string }) {
  const normalized = normalizeMessageText(text);
  if (!normalized) return null;

  const rawLines = normalized.split("\n");

  type Block =
    | { type: "paragraph"; lines: string[] }
    | { type: "bullet-list"; items: string[] }
    | { type: "numbered-list"; items: string[] };

  const blocks: Block[] = [];
  let currentParagraph: string[] = [];

  const flushParagraph = () => {
    if (currentParagraph.length > 0) {
      blocks.push({ type: "paragraph", lines: [...currentParagraph] });
      currentParagraph = [];
    }
  };

  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i];
    const trimmed = line.trim();

    if (trimmed === "") {
      flushParagraph();
      continue;
    }

    // Item de lista não-ordenada (- ou *)
    const bulletMatch = trimmed.match(/^[-*•]\s+(.*)$/);
    if (bulletMatch) {
      flushParagraph();
      const lastBlock = blocks[blocks.length - 1];
      if (lastBlock && lastBlock.type === "bullet-list") {
        lastBlock.items.push(bulletMatch[1]);
      } else {
        blocks.push({ type: "bullet-list", items: [bulletMatch[1]] });
      }
      continue;
    }

    // Item de lista numerada (1. , 2. )
    const numberMatch = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
    if (numberMatch) {
      flushParagraph();
      const lastBlock = blocks[blocks.length - 1];
      if (lastBlock && lastBlock.type === "numbered-list") {
        lastBlock.items.push(numberMatch[2]);
      } else {
        blocks.push({ type: "numbered-list", items: [numberMatch[2]] });
      }
      continue;
    }

    currentParagraph.push(trimmed);
  }

  flushParagraph();

  return (
    <div className="guia-formatted-content">
      {blocks.map((block, bIdx) => {
        if (block.type === "bullet-list") {
          return (
            <ul key={`b-${bIdx}`} className="guia-list guia-bullet-list">
              {block.items.map((item, itemIdx) => (
                <li key={`b-${bIdx}-${itemIdx}`}>{renderInline(item, `b-${bIdx}-${itemIdx}`)}</li>
              ))}
            </ul>
          );
        }

        if (block.type === "numbered-list") {
          return (
            <ol key={`n-${bIdx}`} className="guia-list guia-numbered-list">
              {block.items.map((item, itemIdx) => (
                <li key={`n-${bIdx}-${itemIdx}`}>{renderInline(item, `n-${bIdx}-${itemIdx}`)}</li>
              ))}
            </ol>
          );
        }

        // Parágrafo
        return (
          <p key={`p-${bIdx}`}>
            {block.lines.map((line, lIdx) => (
              <React.Fragment key={`p-${bIdx}-${lIdx}`}>
                {lIdx > 0 ? <br /> : null}
                {renderInline(line, `p-${bIdx}-${lIdx}`)}
              </React.Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
