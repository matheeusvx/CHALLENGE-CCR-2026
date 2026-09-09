"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Leaf, Sparkles, X } from "lucide-react";
import { ONBOARDING_TOTAL_STEPS, useOnboardingStore } from "@/stores/onboarding-store";
import type { AppView } from "./app-sidebar";

// ---------------------------------------------------------------------------
// Tour step definitions
// ---------------------------------------------------------------------------

export type TourStep = {
  /** data-tour attribute value to spotlight, or null for centered step. */
  target: string | null;
  title: string;
  text: string;
  /** Preferred card placement relative to target. */
  placement: "center" | "right" | "left" | "bottom" | "top";
  /** If the tour requires navigating to a specific AppView for this step. */
  requiredView?: AppView;
  /** If this step requires automatically opening a popover menu (e.g., operator menu). */
  autoOpenMenu?: "operator" | null;
  /** If this step requires temporarily expanding the sidebar. */
  expandSidebar?: boolean;
};

export const TOUR_STEPS: TourStep[] = [
  {
    target: null,
    title: "Bem-vindo ao Motiva Faixa Verde",
    text: "Monitoramento inteligente da vegetação lateral rodoviária com análise geoespacial e dados orbitais Sentinel para apoio à tomada de decisão operacional.",
    placement: "center",
  },
  {
    target: "map",
    title: "Mapa operacional",
    text: "Navegue pelo traçado rodoviário com pan e zoom. O mapa apresenta o contexto operacional da rodovia, faixas de domínio e áreas com potencial intervenção.",
    placement: "center",
    requiredView: "analysis",
  },
  {
    target: "auto-analysis",
    title: "Análise automática",
    text: "Ao navegar e aproximar a visualização da rodovia, o sistema inicia análises automaticamente. O resultado surge na aba Resultado sem você precisar desenhar uma área.",
    placement: "bottom",
    requiredView: "analysis",
  },
  {
    target: "drawing-tools",
    title: "Análise manual",
    text: "Utilize as ferramentas de desenho no mapa sempre que desejar delimitar um polígono customizado para avaliação ou reanálise pontual de um trecho específico.",
    placement: "bottom",
    requiredView: "analysis",
  },
  {
    target: "result-panel",
    title: "Painel de resultados",
    text: "Consulte a recomendação operacional (Cortar, Não cortar ou Inconclusivo), qualidade dos dados reais disponíveis, período e parecer multissensor Sentinel-1 e Sentinel-2.",
    placement: "left",
    requiredView: "analysis",
  },
  {
    target: "nav-history",
    title: "Histórico de análises",
    text: "Todas as avaliações geradas — automáticas ou manuais — ficam salvas no Histórico para consulta posterior, comparação temporal e auditoria operacional.",
    placement: "right",
    requiredView: "analysis",
  },
  {
    target: "guia-widget",
    title: "gu.ia — Assistente virtual",
    text: "Assistente virtual da plataforma para apoiar o operador na interpretação da interface, resultados e uso das ferramentas disponíveis no sistema.",
    placement: "right",
  },
  {
    target: "operator-profile",
    title: "Conta e perfil",
    text: "Acesse seu perfil de operador para personalizar nome, e-mail, avatar, cargo e organização, além de acompanhar suas estatísticas de uso.",
    placement: "right",
  },
  {
    target: "operator-menu-settings",
    title: "Configurações da plataforma",
    text: "No menu da sua conta, acesse Configurações para ajustar preferências visuais, cores das rodovias, modo do assistente e reiniciar este tutorial.",
    placement: "right",
    autoOpenMenu: "operator",
    expandSidebar: true,
  },
  {
    target: null,
    title: "Pronto para começar!",
    text: "Você concluiu o tour pelas funcionalidades essenciais. Navegue livremente pelo mapa ou selecione uma área para iniciar os trabalhos.",
    placement: "center",
  },
];

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

type Rect = { top: number; left: number; width: number; height: number };

const SPOT_PAD = 6;
const SPOT_RADIUS = 10;
const CARD_GAP = 14;
const CARD_WIDTH = 380;
const CARD_HEIGHT = 270;
const CARD_MIN_MARGIN = 16;

function getTargetRect(target: string): Rect | null {
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  return {
    top: r.top - SPOT_PAD,
    left: r.left - SPOT_PAD,
    width: r.width + SPOT_PAD * 2,
    height: r.height + SPOT_PAD * 2,
  };
}

function svgClipPath(rect: Rect | null, vw: number, vh: number): string {
  // Full-screen rect (clockwise) with optional inner cutout (counter-clockwise)
  const outer = `M0,0 H${vw} V${vh} H0 Z`;
  if (!rect) return outer;
  const { top: y, left: x, width: w, height: h } = rect;
  const r = Math.min(SPOT_RADIUS, w / 2, h / 2);
  // Inner rounded rect, counter-clockwise for even-odd cut
  const inner = [
    `M${x + r},${y}`,
    `H${x + w - r}`,
    `Q${x + w},${y} ${x + w},${y + r}`,
    `V${y + h - r}`,
    `Q${x + w},${y + h} ${x + w - r},${y + h}`,
    `H${x + r}`,
    `Q${x},${y + h} ${x},${y + h - r}`,
    `V${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `Z`,
  ].join(" ");
  return `${outer} ${inner}`;
}

type CardPosition = { top: number; left: number };

function computeCardPosition(
  rect: Rect | null,
  placement: TourStep["placement"],
  vw: number,
  vh: number,
): CardPosition {
  const effectiveWidth = Math.min(CARD_WIDTH, vw - CARD_MIN_MARGIN * 2);
  const effectiveHeight = Math.min(CARD_HEIGHT, vh - CARD_MIN_MARGIN * 2);

  if (!rect || placement === "center") {
    return {
      top: Math.max(CARD_MIN_MARGIN, Math.round((vh - effectiveHeight) / 2)),
      left: Math.max(CARD_MIN_MARGIN, Math.round((vw - effectiveWidth) / 2)),
    };
  }

  let top: number;
  let left: number;

  switch (placement) {
    case "right":
      top = Math.round(rect.top + (rect.height - effectiveHeight) / 2);
      left = Math.round(rect.left + rect.width + CARD_GAP);
      break;
    case "left":
      top = Math.round(rect.top + (rect.height - effectiveHeight) / 2);
      left = Math.round(rect.left - effectiveWidth - CARD_GAP);
      break;
    case "bottom":
      top = Math.round(rect.top + rect.height + CARD_GAP);
      left = Math.round(rect.left + (rect.width - effectiveWidth) / 2);
      break;
    case "top":
      top = Math.round(rect.top - effectiveHeight - CARD_GAP);
      left = Math.round(rect.left + (rect.width - effectiveWidth) / 2);
      break;
  }

  // Clamp within viewport
  if (left + effectiveWidth > vw - CARD_MIN_MARGIN) {
    left = vw - effectiveWidth - CARD_MIN_MARGIN;
  }
  if (left < CARD_MIN_MARGIN) left = CARD_MIN_MARGIN;
  if (top + effectiveHeight > vh - CARD_MIN_MARGIN) {
    top = vh - effectiveHeight - CARD_MIN_MARGIN;
  }
  if (top < CARD_MIN_MARGIN) top = CARD_MIN_MARGIN;

  return { top, left };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type OnboardingTourProps = {
  onNavigate: (view: AppView) => void;
};

export function OnboardingTour({ onNavigate }: OnboardingTourProps) {
  const tourActive = useOnboardingStore((s) => s.tourActive);
  const currentStep = useOnboardingStore((s) => s.currentStep);
  const nextStep = useOnboardingStore((s) => s.nextStep);
  const prevStep = useOnboardingStore((s) => s.prevStep);
  const skipTour = useOnboardingStore((s) => s.skipTour);
  const completeTour = useOnboardingStore((s) => s.completeTour);

  const cardRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const [spotRect, setSpotRect] = useState<Rect | null>(null);
  const [cardPos, setCardPos] = useState<CardPosition>({ top: 0, left: 0 });
  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 });

  const step = TOUR_STEPS[currentStep];
  const isFirst = currentStep === 0;
  const isLast = currentStep === ONBOARDING_TOTAL_STEPS - 1;

  // Measure and position
  const recalculate = useCallback(() => {
    if (!tourActive) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    setViewportSize({ w: vw, h: vh });

    const step = TOUR_STEPS[useOnboardingStore.getState().currentStep];
    const rect = step.target ? getTargetRect(step.target) : null;
    setSpotRect(rect);
    setCardPos(computeCardPosition(rect, step.placement, vw, vh));
  }, [tourActive]);

  // Control popover menu open/close and sidebar expansion based on step definition
  useEffect(() => {
    if (!tourActive) {
      useOnboardingStore.getState().setOperatorMenuOpen(false);
      useOnboardingStore.getState().setTourSidebarExpanded(false);
      return;
    }
    const shouldOpen = step?.autoOpenMenu === "operator";
    useOnboardingStore.getState().setOperatorMenuOpen(shouldOpen);
    const shouldExpand = Boolean(step?.expandSidebar);
    useOnboardingStore.getState().setTourSidebarExpanded(shouldExpand);
  }, [tourActive, currentStep, step?.autoOpenMenu, step?.expandSidebar]);

  // Clean up any open tour popovers or sidebar expansions on unmount
  useEffect(() => {
    return () => {
      useOnboardingStore.getState().setOperatorMenuOpen(false);
      useOnboardingStore.getState().setTourSidebarExpanded(false);
    };
  }, []);

  // Navigate to required view before measuring
  useEffect(() => {
    if (!tourActive) return;
    if (step.requiredView) {
      onNavigate(step.requiredView === "analysis" ? "analysis" : step.requiredView);
    }
    // Delay measurement to allow DOM to settle after view transition or menu open
    const timer = setTimeout(recalculate, 80);
    const backupTimer = setTimeout(recalculate, 220);
    return () => {
      clearTimeout(timer);
      clearTimeout(backupTimer);
    };
  }, [tourActive, currentStep, step.requiredView, onNavigate, recalculate]);

  // Resize / scroll listeners
  useEffect(() => {
    if (!tourActive) return;
    const handler = () => recalculate();
    window.addEventListener("resize", handler);
    window.addEventListener("scroll", handler, true);
    return () => {
      window.removeEventListener("resize", handler);
      window.removeEventListener("scroll", handler, true);
    };
  }, [tourActive, recalculate]);

  // ResizeObserver on target element
  useEffect(() => {
    if (!tourActive || !step.target) return;
    const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
    if (!el) return;
    const observer = new ResizeObserver(() => recalculate());
    observer.observe(el);
    return () => observer.disconnect();
  }, [tourActive, step.target, recalculate]);

  // Focus management
  useEffect(() => {
    if (tourActive) {
      previousFocusRef.current = document.activeElement as HTMLElement;
      requestAnimationFrame(() => cardRef.current?.focus());
    } else if (previousFocusRef.current) {
      previousFocusRef.current.focus();
      previousFocusRef.current = null;
    }
  }, [tourActive]);

  // Re-focus card on step change
  useEffect(() => {
    if (tourActive) {
      requestAnimationFrame(() => cardRef.current?.focus());
    }
  }, [tourActive, currentStep]);

  // Keyboard handling
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        skipTour();
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (isLast) completeTour();
        else nextStep();
      }
    },
    [skipTour, nextStep, completeTour, isLast],
  );

  if (!tourActive) return null;

  return (
    <div className="onboarding-overlay" onClick={skipTour}>
      {/* SVG spotlight overlay */}
      <svg
        className="onboarding-spotlight-svg"
        width={viewportSize.w}
        height={viewportSize.h}
        viewBox={`0 0 ${viewportSize.w} ${viewportSize.h}`}
        aria-hidden="true"
      >
        <path
          d={svgClipPath(spotRect, viewportSize.w, viewportSize.h)}
          fillRule="evenodd"
          className="onboarding-spotlight-path"
        />
      </svg>

      {/* Card */}
      <div
        ref={cardRef}
        className="onboarding-card"
        role="dialog"
        aria-modal="true"
        aria-label={step.title}
        tabIndex={-1}
        style={{ top: cardPos.top, left: cardPos.left }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="onboarding-card-header">
          <div className="onboarding-card-icon">
            {isFirst ? <Leaf size={20} /> : isLast ? <Sparkles size={18} /> : <BookOpen size={18} />}
          </div>
          <button
            type="button"
            className="onboarding-close"
            aria-label="Fechar tutorial"
            onClick={skipTour}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="onboarding-card-body">
          <h2 className="onboarding-card-title">{step.title}</h2>
          <p className="onboarding-card-text">{step.text}</p>
        </div>

        {/* Footer */}
        <div className="onboarding-card-footer">
          <span className="onboarding-progress" aria-live="polite">
            {currentStep + 1} de {ONBOARDING_TOTAL_STEPS}
          </span>
          <div className="onboarding-card-actions">
            {!isFirst && (
              <button
                type="button"
                className="onboarding-btn onboarding-btn-secondary"
                onClick={prevStep}
              >
                <ChevronLeft size={15} />
                Anterior
              </button>
            )}
            {isLast ? (
              <button
                type="button"
                className="onboarding-btn onboarding-btn-primary"
                onClick={completeTour}
              >
                Começar agora
              </button>
            ) : (
              <button
                type="button"
                className="onboarding-btn onboarding-btn-primary"
                onClick={nextStep}
              >
                Próximo
                <ChevronRight size={15} />
              </button>
            )}
          </div>
        </div>

        {/* Skip link */}
        <button
          type="button"
          className="onboarding-skip"
          onClick={skipTour}
        >
          Pular tutorial
        </button>
      </div>
    </div>
  );
}
