"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { HistoryView } from "@/components/views/history-view";
import { SettingsView } from "@/components/views/settings-view";
import { SourcesView } from "@/components/views/sources-view";
import { AppHeader } from "./app-header";
import { AppSidebar, type AppView } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const [activeView, setActiveView] = useState<AppView>("analysis");

  return (
    <div className="app-shell">
      <AppSidebar activeView={activeView} onNavigate={setActiveView} />
      <div className="main-column">
        <AppHeader />
        <main className="workspace-main">
          <div className={activeView === "analysis" ? "shell-view active" : "shell-view inactive"} aria-hidden={activeView !== "analysis"}>
            {children}
          </div>
          {activeView === "history" ? <HistoryView onNavigate={setActiveView} /> : null}
          {activeView === "sources" ? <SourcesView /> : null}
          {activeView === "settings" ? <SettingsView /> : null}
        </main>
      </div>
    </div>
  );
}
