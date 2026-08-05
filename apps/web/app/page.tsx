import { AppShell } from "@/components/layout/app-shell";
import { GeospatialWorkspace } from "@/components/workspace/geospatial-workspace";

export default function Home() {
  return (
    <AppShell>
      <GeospatialWorkspace />
    </AppShell>
  );
}
