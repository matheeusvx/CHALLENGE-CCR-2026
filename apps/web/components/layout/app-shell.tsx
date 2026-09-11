"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountView } from "@/components/views/account-view";
import { AlertsView } from "@/components/views/alerts-view";
import { HistoryView } from "@/components/views/history-view";
import { SettingsView } from "@/components/views/settings-view";
import { SourcesView } from "@/components/views/sources-view";
import { ValidationView } from "@/components/views/validation-view";
import type { AlertMapTarget } from "@/lib/schemas/alerts";
import { useAnalysisStore } from "@/stores/analysis-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useSettingsStore } from "@/stores/settings-store";
import { useGuiaStore } from "@/stores/guia-store";
import { AppHeader } from "./app-header";
import { OnboardingTour } from "./onboarding-tour";
import { AppSidebar, type AppView } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const [activeView, setActiveView] = useState<AppView>("analysis");
  const resetAnalysisSession = useAnalysisStore((state) => state.resetAnalysisSession);
  const onboardingCompleted = useOnboardingStore((s) => s.onboardingCompleted);
  const startTour = useOnboardingStore((s) => s.startTour);
  const tourActive = useOnboardingStore((s) => s.tourActive);
  const tourSidebarExpanded = useOnboardingStore((s) => s.tourSidebarExpanded);
  const sidebarCollapsed = useSettingsStore((s) => s.sidebarCollapsed);
  const sidebarHydrated = useSettingsStore((s) => s.sidebarHydrated);
  const isSidebarExpanded = sidebarHydrated
    ? tourActive && tourSidebarExpanded
      ? true
      : !sidebarCollapsed
    : false;
  const isGuiaOpen = useGuiaStore((s) => s.isOpen);
  const guiaDisplayMode = useGuiaStore((s) => s.displayMode);
  const isGuiaDocked = isGuiaOpen && guiaDisplayMode === "docked";

  const startNewAnalysis = () => {
    resetAnalysisSession();
    setActiveView("analysis");
  };

  const handleNavigation = (view: AppView) => {
    if (view === "analysis") startNewAnalysis();
    else setActiveView(view);
  };

  const handleOpenTrack = (target: AlertMapTarget) => {
    useAnalysisStore.getState().focusAlertTarget(target);
    setActiveView("analysis");
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
    <div className={`app-shell ${isSidebarExpanded ? "sidebar--expanded" : "sidebar--collapsed"} ${isGuiaDocked ? "has-docked-guia" : ""}`}>
      <AppSidebar activeView={activeView} onNavigate={handleNavigation} />
      <div className="main-column">
        <AppHeader />
        <main className="workspace-main">
          <div className={activeView === "analysis" ? "shell-view active" : "shell-view inactive"} aria-hidden={activeView !== "analysis"}>
            {children}
          </div>
          {activeView === "history" ? <HistoryView onStartNewAnalysis={startNewAnalysis} onOpenWorkspace={() => setActiveView("analysis")} /> : null}
          {activeView === "alerts" ? <AlertsView onOpenTrack={handleOpenTrack} /> : null}
          {activeView === "sources" ? <SourcesView /> : null}
          {activeView === "validation" ? <ValidationView onStartNewAnalysis={startNewAnalysis} /> : null}
          {activeView === "account" ? <AccountView onNavigate={handleNavigation} /> : null}
          {activeView === "settings" ? <SettingsView onNavigate={handleNavigation} /> : null}
        </main>
      </div>
      <OnboardingTour onNavigate={handleTourNavigate} />
    </div>
  );
}
