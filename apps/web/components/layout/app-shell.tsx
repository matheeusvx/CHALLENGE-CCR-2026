"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { HistoryView } from "@/components/views/history-view";
import { SettingsView } from "@/components/views/settings-view";
import { SourcesView } from "@/components/views/sources-view";
import { useAnalysisStore } from "@/stores/analysis-store";
import { AppHeader } from "./app-header";
import { AppSidebar, type AppView } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const [activeView, setActiveView] = useState<AppView>("analysis");
  const resetAnalysisSession = useAnalysisStore((state) => state.resetAnalysisSession);

  const startNewAnalysis = () => {
    resetAnalysisSession();
    setActiveView("analysis");
  };

  const handleNavigation = (view: AppView) => {
    if (view === "analysis") startNewAnalysis();
    else setActiveView(view);
  };

  return (
    <div className="app-shell">
      <AppSidebar activeView={activeView} onNavigate={handleNavigation} />
      <div className="main-column">
        <AppHeader />
        <main className="workspace-main">
          <div className={activeView === "analysis" ? "shell-view active" : "shell-view inactive"} aria-hidden={activeView !== "analysis"}>
            {children}
          </div>
          {activeView === "history" ? <HistoryView onStartNewAnalysis={startNewAnalysis} onOpenWorkspace={() => setActiveView("analysis")} /> : null}
          {activeView === "sources" ? <SourcesView /> : null}
          {activeView === "settings" ? <SettingsView /> : null}
        </main>
      </div>
    </div>
  );
}
