"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface GuiaLayout {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const GUIA_LAYOUT_STORAGE_KEY = "motiva-guia-layout-v1";
export const MIN_WIDTH = 320;
export const MIN_HEIGHT = 360;
export const DEFAULT_WIDTH = 440;
export const DEFAULT_HEIGHT = 580;

/**
 * Calcula o layout padrão confortável com base na viewport atual.
 * Inicia ancorado à direita da barra lateral Motiva (ou no topo em mobile).
 */
export function getDefaultLayout(): GuiaLayout {
  if (typeof window === "undefined") {
    return { x: 236, y: 100, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT };
  }

  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Mobile
  if (vw <= 720) {
    const width = Math.max(MIN_WIDTH, Math.min(DEFAULT_WIDTH, vw - 24));
    const height = Math.max(MIN_HEIGHT, Math.min(DEFAULT_HEIGHT, vh - 84));
    return {
      x: Math.max(8, Math.round((vw - width) / 2)),
      y: 66,
      width,
      height,
    };
  }

  // Tablet
  if (vw <= 1120) {
    const width = Math.max(MIN_WIDTH, Math.min(DEFAULT_WIDTH, vw - 100));
    const height = Math.max(MIN_HEIGHT, Math.min(DEFAULT_HEIGHT, vh - 32));
    return {
      x: 86,
      y: Math.max(16, vh - height - 16),
      width,
      height,
    };
  }

  // Desktop
  const width = Math.max(MIN_WIDTH, Math.min(DEFAULT_WIDTH, vw - 260));
  const height = Math.max(MIN_HEIGHT, Math.min(DEFAULT_HEIGHT, vh - 32));
  return {
    x: 236,
    y: Math.max(16, vh - height - 16),
    width,
    height,
  };
}

/**
 * Mantém a janela rigorosamente dentro dos limites seguros da viewport:
 * - O header (pelo menos 50px de altura) nunca pode sumir no fundo da tela.
 * - O topo da janela não pode ficar acima da tela (y >= 8).
 * - A janela não pode vazar completamente pelas laterais.
 */
export function clampLayout(layout: GuiaLayout, vw: number, vh: number): GuiaLayout {
  const minW = Math.min(MIN_WIDTH, vw - 16);
  const minH = Math.min(MIN_HEIGHT, vh - 16);

  const width = Math.max(minW, Math.min(layout.width, vw - 16));
  const height = Math.max(minH, Math.min(layout.height, vh - 16));

  const maxX = Math.max(8, vw - width - 8);
  const maxY = Math.max(8, vh - 60);

  const x = Math.max(8, Math.min(layout.x, maxX));
  const y = Math.max(8, Math.min(layout.y, maxY));

  return { x, y, width, height };
}

function readStoredLayout(): GuiaLayout | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(GUIA_LAYOUT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      typeof parsed?.x === "number" &&
      typeof parsed?.y === "number" &&
      typeof parsed?.width === "number" &&
      typeof parsed?.height === "number"
    ) {
      return clampLayout(parsed, window.innerWidth, window.innerHeight);
    }
  } catch {
    // Ignora erro de JSON corrompido
  }
  return null;
}

function writeStoredLayout(layout: GuiaLayout) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(GUIA_LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Ignora possíveis erros de quota
  }
}

export function useGuiaLayout() {
  const [layout, setLayout] = useState<GuiaLayout>(() => {
    const stored = readStoredLayout();
    return stored ?? getDefaultLayout();
  });
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);

  const layoutRef = useRef(layout);

  useEffect(() => {
    layoutRef.current = layout;
  }, [layout]);

  const dragStartRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startLeft: number;
    startTop: number;
  } | null>(null);

  const resizeStartRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startWidth: number;
    startHeight: number;
  } | null>(null);

  // Monitora redimensionamento da janela do navegador para ajustar os limites
  useEffect(() => {
    const handleWindowResize = () => {
      setLayout((current) => clampLayout(current, window.innerWidth, window.innerHeight));
    };
    window.addEventListener("resize", handleWindowResize);
    return () => window.removeEventListener("resize", handleWindowResize);
  }, []);

  // Restaurar layout padrão
  const resetLayout = useCallback(() => {
    const defaultLayout = getDefaultLayout();
    setLayout(defaultLayout);
    try {
      window.localStorage.removeItem(GUIA_LAYOUT_STORAGE_KEY);
    } catch {
      // Ignora erro de localStorage
    }
  }, []);

  // --- Handlers de Drag (Movimento pelo Header) ---
  const handleHeaderPointerDown = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return; // apenas clique principal

    // Não inicia drag se clicou em botão de fechar, reset ou input
    const target = e.target as HTMLElement;
    if (target.closest("button, input, textarea, a, [data-no-drag='true']")) {
      return;
    }

    const currentLayout = layoutRef.current;
    dragStartRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startLeft: currentLayout.x,
      startTop: currentLayout.y,
    };

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Fallback gracioso caso browser não suporte capture
    }

    setIsDragging(true);
  }, []);

  const handleHeaderPointerMove = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const drag = dragStartRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;

    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;

    const targetX = drag.startLeft + dx;
    const targetY = drag.startTop + dy;

    const current = layoutRef.current;
    const clamped = clampLayout(
      { ...current, x: targetX, y: targetY },
      window.innerWidth,
      window.innerHeight
    );

    setLayout((prev) => ({
      ...prev,
      x: clamped.x,
      y: clamped.y,
    }));
  }, []);

  const handleHeaderPointerUp = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const drag = dragStartRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;

    dragStartRef.current = null;
    setIsDragging(false);

    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {
      // Ignora erro
    }

    writeStoredLayout(layoutRef.current);
  }, []);

  // --- Handlers de Resize (Canto inferior direito) ---
  const handleResizePointerDown = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    e.stopPropagation();

    const currentLayout = layoutRef.current;
    resizeStartRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startWidth: currentLayout.width,
      startHeight: currentLayout.height,
    };

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Ignora erro
    }

    setIsResizing(true);
  }, []);

  const handleResizePointerMove = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const resizer = resizeStartRef.current;
    if (!resizer || resizer.pointerId !== e.pointerId) return;

    const dw = e.clientX - resizer.startX;
    const dh = e.clientY - resizer.startY;

    const current = layoutRef.current;
    const targetWidth = resizer.startWidth + dw;
    const targetHeight = resizer.startHeight + dh;

    const maxW = Math.max(MIN_WIDTH, window.innerWidth - current.x - 8);
    const maxH = Math.max(MIN_HEIGHT, window.innerHeight - current.y - 8);

    const clampedWidth = Math.max(MIN_WIDTH, Math.min(targetWidth, maxW));
    const clampedHeight = Math.max(MIN_HEIGHT, Math.min(targetHeight, maxH));

    setLayout((prev) => ({
      ...prev,
      width: clampedWidth,
      height: clampedHeight,
    }));
  }, []);

  const handleResizePointerUp = useCallback((e: React.PointerEvent<HTMLElement>) => {
    const resizer = resizeStartRef.current;
    if (!resizer || resizer.pointerId !== e.pointerId) return;

    resizeStartRef.current = null;
    setIsResizing(false);

    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {
      // Ignora erro
    }

    writeStoredLayout(layoutRef.current);
  }, []);

  return {
    layout,
    isDragging,
    isResizing,
    resetLayout,
    headerDragProps: {
      onPointerDown: handleHeaderPointerDown,
      onPointerMove: handleHeaderPointerMove,
      onPointerUp: handleHeaderPointerUp,
      onPointerCancel: handleHeaderPointerUp,
    },
    resizeHandleProps: {
      onPointerDown: handleResizePointerDown,
      onPointerMove: handleResizePointerMove,
      onPointerUp: handleResizePointerUp,
      onPointerCancel: handleResizePointerUp,
    },
  };
}
