"use client";

import { useQuery } from "@tanstack/react-query";
import { AppWindow, Bell, BookOpen, ExternalLink, Eye, EyeOff, Globe, Info, Map as MapIcon, Maximize2, Monitor, Moon, Palette, PanelRight, RotateCcw, ShieldCheck, Sparkles, Sun, User } from "lucide-react";
import type { ComponentType } from "react";
import { APP_ENVIRONMENT, APP_NAME, APP_VERSION } from "@/lib/app-config";
import { getHealth } from "@/lib/api/analyses";
import { useSettingsStore, type ThemePreference } from "@/stores/settings-store";
import { useGuiaStore, type GuiaDisplayMode } from "@/stores/guia-store";
import { useRoadColorStore, MOTIVA_ROADS } from "@/stores/road-color-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import type { AppView } from "@/components/layout/app-sidebar";

const themeOptions: Array<{ id: ThemePreference; label: string; icon: ComponentType<{ size?: number }> }> = [
  { id: "light", label: "Claro", icon: Sun },
  { id: "dark", label: "Escuro", icon: Moon },
  { id: "system", label: "Sistema", icon: Monitor },
];

const guiaDisplayModeOptions: Array<{
  id: GuiaDisplayMode;
  label: string;
  description: string;
  icon: ComponentType<{ size?: number }>;
}> = [
  {
    id: "floating",
    label: "Flutuante",
    description: "Janela móvel e redimensionável.",
    icon: AppWindow,
  },
  {
    id: "docked",
    label: "Painel fixo",
    description: "Mantém a gu.ia aberta ao lado do conteúdo.",
    icon: PanelRight,
  },
  {
    id: "expanded",
    label: "Tela ampla",
    description: "Abre o assistente em uma área maior para conversas extensas.",
    icon: Maximize2,
  },
];

export function SettingsView({ onNavigate }: { onNavigate?: (view: AppView) => void }) {
  const themePreference = useSettingsStore((state) => state.themePreference);
  const setThemePreference = useSettingsStore((state) => state.setThemePreference);
  const notificationsEnabled = useSettingsStore((state) => state.notificationsEnabled);
  const setNotificationsEnabled = useSettingsStore((state) => state.setNotificationsEnabled);
  const guiaDisplayMode = useGuiaStore((state) => state.displayMode);
  const setGuiaDisplayMode = useGuiaStore((state) => state.setDisplayMode);
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 60_000, retry: 1 });
  const online = health.data?.status === "ok";

  const resetOnboarding = useOnboardingStore((s) => s.resetOnboarding);
  const startTour = useOnboardingStore((s) => s.startTour);

  const handleRestartTutorial = () => {
    resetOnboarding();
    startTour();
    onNavigate?.("analysis");
  };

  const roadColors = useRoadColorStore((state) => state.activeColors);
  const roadVisibility = useRoadColorStore((state) => state.activeVisibility);
  const setRoadColor = useRoadColorStore((state) => state.setRoadColor);
  const resetRoadColor = useRoadColorStore((state) => state.resetRoadColor);
  const setRoadVisible = useRoadColorStore((state) => state.setRoadVisible);
  const showAllRoads = useRoadColorStore((state) => state.showAllRoads);
  const hideAllRoads = useRoadColorStore((state) => state.hideAllRoads);
  const resetAllDefaults = useRoadColorStore((state) => state.resetAllDefaults);

  return (
    <section className="secondary-view" aria-labelledby="secondary-view-settings">
      <header>
        <span>Preferências operacionais</span>
        <h1 id="secondary-view-settings">Configurações</h1>
        <p>Ajuste a aparência do painel e consulte as informações da conta e do aplicativo.</p>
      </header>

      <div className="settings-grid">
        {/* Aparência */}
        <section className="settings-card">
          <div className="settings-card-head"><Palette size={17} aria-hidden="true" /><div><strong>Aparência</strong><span>Escolha o tema do painel.</span></div></div>
          <div className="theme-choice" role="radiogroup" aria-label="Tema do painel">
            {themeOptions.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={themePreference === id}
                className={themePreference === id ? "active" : ""}
                onClick={() => setThemePreference(id)}
              >
                <Icon size={16} />{label}
              </button>
            ))}
          </div>
        </section>

        {/* GuIA */}
        <section className="settings-card">
          <div className="settings-card-head">
            <Sparkles size={17} aria-hidden="true" />
            <div>
              <strong>gu.ia</strong>
              <span>Escolha como o assistente será exibido no painel.</span>
            </div>
          </div>
          <div className="guia-settings-options" role="radiogroup" aria-label="Modo de exibição da gu.ia">
            {guiaDisplayModeOptions.map(({ id, label, description, icon: Icon }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={guiaDisplayMode === id}
                className={`guia-settings-option ${guiaDisplayMode === id ? "active" : ""}`}
                onClick={() => setGuiaDisplayMode(id)}
              >
                <span className="guia-settings-option-icon" aria-hidden="true">
                  <Icon size={18} />
                </span>
                <div className="guia-settings-option-text">
                  <strong>{label}</strong>
                  <span>{description}</span>
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Conta */}
        <section className="settings-card">
          <div className="settings-card-head">
            <User size={17} aria-hidden="true" />
            <div>
              <strong>Conta</strong>
              <span>Gerencie seu perfil, preferências da conta e produtividade.</span>
            </div>
          </div>
          <p className="settings-card-desc">
            Acesse a área dedicada para editar seus dados de operador, foto de perfil e consultar relatórios operacionais.
          </p>
          <div className="settings-card-actions">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onNavigate?.("account")}
            >
              <ExternalLink size={14} aria-hidden="true" />
              Abrir minha conta
            </button>
          </div>
        </section>

        {/* Preferências */}
        <section className="settings-card">
          <div className="settings-card-head"><Bell size={17} aria-hidden="true" /><div><strong>Preferências</strong><span>Idioma e notificações.</span></div></div>
          <div className="settings-row">
            <span className="settings-row-label"><Globe size={15} aria-hidden="true" />Idioma</span>
            <span className="settings-row-value">Português (Brasil)</span>
          </div>
          <div className="settings-row">
            <span className="settings-row-label"><Bell size={15} aria-hidden="true" />Notificações de conclusão</span>
            <button
              type="button"
              role="switch"
              aria-checked={notificationsEnabled}
              className={`toggle ${notificationsEnabled ? "on" : ""}`}
              onClick={() => setNotificationsEnabled(!notificationsEnabled)}
            >
              <span className="toggle-knob" />
              <span className="sr-only">{notificationsEnabled ? "Notificações ativadas" : "Notificações desativadas"}</span>
            </button>
          </div>
        </section>

        {/* Tutorial do sistema */}
        <section className="settings-card">
          <div className="settings-card-head"><BookOpen size={17} aria-hidden="true" /><div><strong>Tutorial do sistema</strong><span>Veja novamente a apresentação guiada das principais funcionalidades.</span></div></div>
          <div className="settings-row">
            <span className="settings-row-label"><BookOpen size={15} aria-hidden="true" />Apresentação guiada</span>
            <button
              type="button"
              className="secondary-button"
              onClick={handleRestartTutorial}
              data-testid="restart-tutorial"
            >
              Refazer tutorial
            </button>
          </div>
        </section>

        {/* Sobre o aplicativo */}
        <section className="settings-card">
          <div className="settings-card-head"><Info size={17} aria-hidden="true" /><div><strong>Sobre o aplicativo</strong><span>Versão e conexão.</span></div></div>
          <dl className="about-fields">
            <div><dt>Aplicativo</dt><dd>{APP_NAME}</dd></div>
            <div><dt>Versão</dt><dd>{APP_VERSION}</dd></div>
            <div><dt>Ambiente</dt><dd>{APP_ENVIRONMENT}</dd></div>
            <div>
              <dt>Conexão com a API</dt>
              <dd className="about-status">
                <span className={online ? "status-dot online" : "status-dot"} aria-hidden="true" />
                {health.isLoading ? "Verificando" : online ? `Operacional${health.data?.version ? ` · v${health.data.version}` : ""}` : "Indisponível"}
              </dd>
            </div>
          </dl>
          <p className="settings-foot"><ShieldCheck size={14} aria-hidden="true" />Ambiente operacional interno da CCR Motiva.</p>
        </section>

        {/* ── Bottom row: Rodovias full-width ── */}
        <section className="settings-card settings-card--full">
          <div className="settings-card-head">
            <MapIcon size={17} aria-hidden="true" />
            <div>
              <strong>Rodovias</strong>
              <span>Visibilidade e cor de cada rodovia Motiva no mapa.</span>
            </div>
            {/* Global actions */}
            <div className="road-global-actions">
              <button type="button" className="road-action-btn" onClick={showAllRoads}>
                Mostrar todas
              </button>
              <button type="button" className="road-action-btn" onClick={hideAllRoads}>
                Ocultar todas
              </button>
              <button type="button" className="road-action-btn road-action-btn--reset" onClick={resetAllDefaults}>
                Restaurar padrões
              </button>
            </div>
          </div>

          <div className="road-list" role="list">
            {MOTIVA_ROADS.map((road) => {
              const visible = roadVisibility[road.key] !== false;
              const color = roadColors[road.key] ?? road.defaultColor;
              const displayRef = road.ref ?? "Sem referência SP";
              const toggleLabel = visible
                ? `Ocultar ${road.name}`
                : `Mostrar ${road.name}`;

              return (
                <div
                  key={road.key}
                  className={`road-row${visible ? "" : " road-row--hidden"}`}
                  role="listitem"
                >
                  {/* Visibility toggle */}
                  <button
                    type="button"
                    className="road-visibility-btn"
                    aria-label={toggleLabel}
                    aria-pressed={visible}
                    title={toggleLabel}
                    onClick={() => setRoadVisible(road.key, !visible)}
                  >
                    {visible
                      ? <Eye size={16} aria-hidden="true" />
                      : <EyeOff size={16} aria-hidden="true" />}
                  </button>

                  {/* Color swatch (visual anchor) */}
                  <span
                    className="road-color-swatch"
                    style={{ backgroundColor: color }}
                    aria-hidden="true"
                  />

                  {/* Road identity */}
                  <div className="road-info">
                    <strong className="road-name">{road.name}</strong>
                    <span className="road-ref">{displayRef}</span>
                  </div>

                  {/* Color picker */}
                  <label className="road-color-label" title="Escolher cor">
                    <span className="sr-only">Cor de {road.name}</span>
                    <input
                      type="color"
                      value={color}
                      onChange={(e) => setRoadColor(road.key, e.target.value)}
                      className="road-color-input"
                      disabled={!visible}
                    />
                  </label>

                  {/* Reset color */}
                  <button
                    type="button"
                    className="road-reset-btn"
                    onClick={() => resetRoadColor(road.key)}
                    title={`Restaurar cor padrão de ${road.name}`}
                    aria-label={`Restaurar cor padrão de ${road.name}`}
                    disabled={!visible}
                  >
                    <RotateCcw size={13} aria-hidden="true" />
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </section>
  );
}
