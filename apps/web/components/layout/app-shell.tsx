import type { ReactNode } from "react";
import { AppHeader } from "./app-header";
import { AppSidebar } from "./app-sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  return <div className="app-shell"><AppSidebar /><div className="main-column"><AppHeader /><main>{children}</main></div></div>;
}
