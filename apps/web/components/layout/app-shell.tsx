"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { SecondaryView } from "@/components/workspace/secondary-view";
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
          {activeView !== "analysis" ? <SecondaryView view={activeView} onBack={() => setActiveView("analysis")} /> : null}
        </main>
      </div>
    </div>
  );
}
