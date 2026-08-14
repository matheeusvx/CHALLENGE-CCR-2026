"use client";

import { useQuery } from "@tanstack/react-query";
import { AtSign, Bell, Globe, Info, Mail, Monitor, Moon, Palette, ShieldCheck, Sun, User } from "lucide-react";
import type { ComponentType } from "react";
import { APP_ENVIRONMENT, APP_NAME, APP_VERSION, mockUser } from "@/lib/app-config";
import { getHealth } from "@/lib/api/analyses";
import { useSettingsStore, type ThemePreference } from "@/stores/settings-store";

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

export function SettingsView() {
  const themePreference = useSettingsStore((state) => state.themePreference);
  const setThemePreference = useSettingsStore((state) => state.setThemePreference);
  const notificationsEnabled = useSettingsStore((state) => state.notificationsEnabled);
  const setNotificationsEnabled = useSettingsStore((state) => state.setNotificationsEnabled);
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 60_000, retry: 1 });
  const online = health.data?.status === "ok";

  return (
    <section className="secondary-view" aria-labelledby="secondary-view-settings">
      <header>
        <span>Preferências operacionais</span>
        <h1 id="secondary-view-settings">Configurações</h1>
        <p>Ajuste a aparência do painel e consulte as informações da conta e do aplicativo.</p>
      </header>

      <div className="settings-grid">
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
      </div>
    </section>
  );
}
