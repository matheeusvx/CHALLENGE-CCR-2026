"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { HistoryView } from "@/components/views/history-view";
import { SettingsView } from "@/components/views/settings-view";
import { SourcesView } from "@/components/views/sources-view";
import { useAnalysisStore } from "@/stores/analysis-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { GuiaWidget } from "@/components/guia/guia-widget";
import { AppHeader } from "./app-header";
import { OnboardingTour } from "./onboarding-tour";
import { AppSidebar, type AppView } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const [activeView, setActiveView] = useState<AppView>("analysis");
  const resetAnalysisSession = useAnalysisStore((state) => state.resetAnalysisSession);
  const onboardingCompleted = useOnboardingStore((s) => s.onboardingCompleted);
  const startTour = useOnboardingStore((s) => s.startTour);

  const startNewAnalysis = () => {
    resetAnalysisSession();
    setActiveView("analysis");
  };

  const handleNavigation = (view: AppView) => {
    if (view === "analysis") startNewAnalysis();
    else setActiveView(view);
  };

  /** Tour navigation — sets the view WITHOUT resetting analysis session. */
  const handleTourNavigate = (view: AppView) => {
    setActiveView(view);
  };

  // Auto-start tour on first access
  useEffect(() => {
    if (!onboardingCompleted) {
      startTour();
    }
    // Only run on mount — intentionally omitting deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          {activeView === "settings" ? <SettingsView onNavigate={handleNavigation} /> : null}
        </main>
      </div>
      <OnboardingTour onNavigate={handleTourNavigate} />
      <GuiaWidget />
    </div>
  );
}
