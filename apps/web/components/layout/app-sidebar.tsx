"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, ChevronsUpDown, Database, FileClock, FlaskConical, Leaf, LogOut, Settings, User, X } from "lucide-react";
import { GuiaWidget } from "@/components/guia/guia-widget";
import { useOperatorProfileStore } from "@/stores/operator-profile-store";

export type AppView = "analysis" | "history" | "sources" | "validation" | "settings" | "account";

const navigation = [
  { id: "analysis", label: "Nova análise", icon: Activity },
  { id: "history", label: "Histórico", icon: FileClock },
  { id: "sources", label: "Fontes de dados", icon: Database },
  { id: "validation", label: "Validação", icon: FlaskConical },
] satisfies Array<{ id: AppView; label: string; icon: typeof Activity }>;

function initialsOf(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function AppSidebar({ activeView, onNavigate }: { activeView: AppView; onNavigate: (view: AppView) => void }) {
  const profile = useOperatorProfileStore();
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoutModalOpen, setLogoutModalOpen] = useState(false);
  const footRef = useRef<HTMLDivElement | null>(null);

  // Fecha menu ao clicar fora
  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (footRef.current && !footRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  // Fecha menu ou modal no Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (menuOpen) setMenuOpen(false);
        if (logoutModalOpen) setLogoutModalOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [menuOpen, logoutModalOpen]);

  return (
    <>
      <aside className="sidebar" aria-label="Navegação principal">
        <div className="brand-mark">
          <span className="brand-icon"><Leaf size={20} aria-hidden="true" /></span>
          <span className="brand-copy"><strong>Motiva</strong><small>Faixa Verde</small></span>
        </div>
        <nav data-tour="navigation">
          {navigation.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={activeView === id ? "nav-item active" : "nav-item"}
              type="button"
              aria-current={activeView === id ? "page" : undefined}
              onClick={() => onNavigate(id)}
              data-tour={id === "analysis" ? "new-analysis" : undefined}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <GuiaWidget />

        {/* Rodapé interativo com perfil do operador */}
        <div className="sidebar-foot" ref={footRef}>
          {menuOpen ? (
            <div className="operator-popover-menu" role="menu" aria-label="Menu da conta" data-testid="operator-popover-menu">
              <button
                type="button"
                role="menuitem"
                className="operator-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  onNavigate("account");
                }}
              >
                <User size={15} aria-hidden="true" />
                <span>Minha conta</span>
              </button>
              <button
                type="button"
                role="menuitem"
                className="operator-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  onNavigate("settings");
                }}
              >
                <Settings size={15} aria-hidden="true" />
                <span>Configurações</span>
              </button>
              <div className="operator-menu-divider" role="separator" />
              <button
                type="button"
                role="menuitem"
                className="operator-menu-item operator-menu-item--logout"
                onClick={() => {
                  setMenuOpen(false);
                  setLogoutModalOpen(true);
                }}
              >
                <LogOut size={15} aria-hidden="true" />
                <span>Sair</span>
              </button>
            </div>
          ) : null}

          <button
            type="button"
            className={`operator-profile-trigger ${activeView === "account" ? "active-view" : ""} ${menuOpen ? "open" : ""}`}
            aria-label={`Menu do operador ${profile.name}`}
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            data-testid="operator-profile-trigger"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <span className="operator-avatar" aria-hidden="true">
              {profile.avatarDataUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={profile.avatarDataUrl} alt="" className="avatar-img" />
              ) : (
                initialsOf(profile.name)
              )}
            </span>
            <div className="operator-identity">
              <strong title={profile.name}>{profile.name}</strong>
              <span title={profile.email}>{profile.email}</span>
            </div>
            <ChevronsUpDown size={14} className="operator-chevron" aria-hidden="true" />
          </button>
        </div>
      </aside>

      {/* Modal honesto de confirmação de logout */}
      {logoutModalOpen ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="logout-title">
          <div className="logout-modal-card">
            <header className="logout-modal-header">
              <h2 id="logout-title">Encerrar sessão neste dispositivo?</h2>
              <button
                type="button"
                className="icon-button modal-close-btn"
                aria-label="Fechar diálogo"
                onClick={() => setLogoutModalOpen(false)}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </header>
            <p className="logout-modal-body">
              Esta versão funciona em modo de demonstração local. Suas análises salvas, preferências operacionais e dados do perfil permanecem armazenados com segurança neste navegador.
            </p>
            <footer className="logout-modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setLogoutModalOpen(false)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="logout-confirm-button"
                data-testid="confirm-logout-btn"
                onClick={() => {
                  setLogoutModalOpen(false);
                  onNavigate("analysis");
                }}
              >
                Encerrar sessão
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </>
  );
}
