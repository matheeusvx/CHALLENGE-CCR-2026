// Reserved IDs for future authoritative Motiva road data. The operational base
// style supplies the current road hierarchy; no simulated roads are added here.
export const MANAGED_ROADS_LAYER_IDS = {
  source: "managed-roads-source",
  casing: "managed-roads-casing",
  line: "managed-roads-line",
  label: "managed-roads-label",
} as const;
