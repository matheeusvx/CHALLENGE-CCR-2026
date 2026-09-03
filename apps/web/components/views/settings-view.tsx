"use client";

import { useQuery } from "@tanstack/react-query";
import { AtSign, Bell, BookOpen, Eye, EyeOff, Globe, Info, Mail, Map as MapIcon, Monitor, Moon, Palette, RotateCcw, ShieldCheck, Sun, User } from "lucide-react";
import type { ComponentType } from "react";
import { APP_ENVIRONMENT, APP_NAME, APP_VERSION, mockUser } from "@/lib/app-config";
import { getHealth } from "@/lib/api/analyses";
import { useSettingsStore, type ThemePreference } from "@/stores/settings-store";
import { useRoadColorStore, MOTIVA_ROADS } from "@/stores/road-color-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import type { AppView } from "@/components/layout/app-sidebar";

const themeOptions: Array<{ id: ThemePreference; label: string; icon: ComponentType<{ size?: number }> }> = [
  { id: "light", label: "Claro", icon: Sun },
  { id: "dark", label: "Escuro", icon: Moon },
  { id: "system", label: "Sistema", icon: Monitor },
];

function initialsOf(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function SettingsView({ onNavigate }: { onNavigate?: (view: AppView) => void }) {
  const themePreference = useSettingsStore((state) => state.themePreference);
  const setThemePreference = useSettingsStore((state) => state.setThemePreference);
  const notificationsEnabled = useSettingsStore((state) => state.notificationsEnabled);
  const setNotificationsEnabled = useSettingsStore((state) => state.setNotificationsEnabled);
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

        {/* Perfil */}
        <section className="settings-card">
          <div className="settings-card-head"><User size={17} aria-hidden="true" /><div><strong>Perfil</strong><span>Dados da conta (demonstração).</span></div></div>
          <div className="profile-card">
            <span className="profile-avatar" aria-hidden="true">{initialsOf(mockUser.name)}</span>
            <div className="profile-identity">
              <strong>{mockUser.name}</strong>
              <span>{mockUser.role}</span>
              <span>{mockUser.organization}</span>
            </div>
          </div>
          <dl className="profile-fields">
            <div><Mail size={15} /><dt>E-mail</dt><dd>{mockUser.email}</dd></div>
            <div><AtSign size={15} /><dt>Login</dt><dd>{mockUser.username}</dd></div>
          </dl>
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
