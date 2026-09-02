"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Leaf, X } from "lucide-react";
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
};

export const TOUR_STEPS: TourStep[] = [
  {
    target: null,
    title: "Bem-vindo ao Motiva Faixa Verde",
    text: "Conheça em poucos passos como monitorar a vegetação e identificar áreas que podem precisar de intervenção.",
    placement: "center",
  },
  {
    target: "new-analysis",
    title: "Nova análise",
    text: "Inicie aqui uma nova avaliação de vegetação em um trecho rodoviário.",
    placement: "right",
    requiredView: "analysis",
  },
  {
    target: "map",
    title: "Mapa operacional",
    text: "Use o mapa para localizar a rodovia e visualizar a área que será monitorada.",
    placement: "right",
    requiredView: "analysis",
  },
  {
    target: "drawing-tools",
    title: "Delimite a área",
    text: "Use as ferramentas de desenho para selecionar a faixa lateral da rodovia que deseja analisar.",
    placement: "bottom",
    requiredView: "analysis",
  },
  {
    target: "area-panel",
    title: "Validação da área",
    text: "Confira a geometria, metragem e cobertura da região antes de executar a análise.",
    placement: "left",
    requiredView: "analysis",
  },
  {
    target: "run-analysis",
    title: "Execute a análise",
    text: "O sistema consulta o histórico de imagens e avalia a evolução recente da vegetação da área selecionada.",
    placement: "left",
    requiredView: "analysis",
  },
  {
    target: "result-panel",
    title: "Entenda o resultado",
    text: "Após a análise, consulte a recomendação, confiança, qualidade dos dados, cobertura efetiva e os motivos que sustentam o resultado.",
    placement: "left",
    requiredView: "analysis",
  },
  {
    target: "navigation",
    title: "Continue acompanhando",
    text: "Consulte análises anteriores, acompanhe as fontes de dados e ajuste as preferências do sistema quando necessário.",
    placement: "right",
  },
];

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

type Rect = { top: number; left: number; width: number; height: number };

const SPOT_PAD = 8;
const SPOT_RADIUS = 8;
const CARD_GAP = 16;
const CARD_WIDTH = 360;
const CARD_MIN_MARGIN = 12;

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
  if (!rect || placement === "center") {
    return {
      top: Math.max(CARD_MIN_MARGIN, (vh - 260) / 2),
      left: Math.max(CARD_MIN_MARGIN, (vw - CARD_WIDTH) / 2),
    };
  }

  const effectiveWidth = Math.min(CARD_WIDTH, vw - CARD_MIN_MARGIN * 2);
  let top: number;
  let left: number;

  switch (placement) {
    case "right":
      top = rect.top;
      left = rect.left + rect.width + CARD_GAP;
      break;
    case "left":
      top = rect.top;
      left = rect.left - effectiveWidth - CARD_GAP;
      break;
    case "bottom":
      top = rect.top + rect.height + CARD_GAP;
      left = rect.left;
      break;
    case "top":
      top = rect.top - 260 - CARD_GAP;
      left = rect.left;
      break;
  }

  // Clamp within viewport
  if (left + effectiveWidth > vw - CARD_MIN_MARGIN) {
    left = vw - effectiveWidth - CARD_MIN_MARGIN;
  }
  if (left < CARD_MIN_MARGIN) left = CARD_MIN_MARGIN;
  if (top < CARD_MIN_MARGIN) top = CARD_MIN_MARGIN;
  // If card would go off bottom, shift up
  if (top + 260 > vh - CARD_MIN_MARGIN) {
    top = vh - 260 - CARD_MIN_MARGIN;
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

  // Navigate to required view before measuring
  useEffect(() => {
    if (!tourActive) return;
    if (step.requiredView) {
      onNavigate(step.requiredView === "analysis" ? "analysis" : step.requiredView);
    }
    // Delay measurement to allow DOM to settle after view transition
    const timer = setTimeout(recalculate, 80);
    return () => clearTimeout(timer);
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
    <div className="onboarding-overlay" aria-hidden="true" onClick={skipTour}>
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
            {isFirst ? <Leaf size={20} /> : <BookOpen size={18} />}
          </div>
          <button
            type="button"
            className="onboarding-close"
            aria-label="Pular tutorial"
            onClick={skipTour}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <h2 className="onboarding-card-title">{step.title}</h2>
        <p className="onboarding-card-text">{step.text}</p>

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
                Concluir
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
