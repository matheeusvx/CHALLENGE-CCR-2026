import type {
  MaintenanceTruth,
  ValidationSource,
  VegetationClass,
} from "@/lib/schemas/validation";

export const VEGETATION_CLASS_LABELS: Record<VegetationClass, string> = {
  low_grass: "Gramado baixo",
  tall_dense_grass: "Gramado alto / denso",
  shrub: "Arbusto",
  tree: "Árvores",
  mixed: "Área mista",
};

export const VEGETATION_CLASS_FORM_OPTIONS: Array<{ value: VegetationClass; label: string }> = [
  { value: "low_grass", label: "Gramado baixo / recém-cortado" },
  { value: "tall_dense_grass", label: "Gramado alto / denso" },
  { value: "shrub", label: "Arbusto" },
  { value: "tree", label: "Árvores" },
  { value: "mixed", label: "Área mista" },
];

export const MAINTENANCE_TRUTH_LABELS: Record<MaintenanceTruth, string> = {
  cut: "Cortar",
  no_cut: "Não cortar",
  uncertain: "Incerto",
};

export const VALIDATION_SOURCE_LABELS: Record<ValidationSource, string> = {
  visual_inspection: "Inspeção visual",
  aerial_imagery: "Imagem aérea",
  field_inspection: "Inspeção de campo",
  maintenance_record: "Registro de manutenção",
  other: "Outro",
};

export function formatVegetationClass(value: string | null | undefined): string {
  if (!value) return "—";
  return VEGETATION_CLASS_LABELS[value as VegetationClass] ?? value;
}

export function formatMaintenanceTruth(value: string | null | undefined): string {
  if (!value) return "—";
  return MAINTENANCE_TRUTH_LABELS[value as MaintenanceTruth] ?? value;
}

export function formatValidationSource(value: string | null | undefined): string {
  if (!value) return "—";
  return VALIDATION_SOURCE_LABELS[value as ValidationSource] ?? value;
}

export function formatMetricValue(
  value: number | null | undefined,
  decimals: number = 2,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatDecibels(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${formatMetricValue(value, 1)} dB`;
}

export function formatPercentileValue(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${Math.round(value)}%`;
}

export function formatValidationDate(value: string | null | undefined): string {
  if (!value) return "—";
  // If YYYY-MM-DD
  const parts = value.split("T")[0].split("-");
  if (parts.length === 3) {
    const [year, month, day] = parts;
    return `${day}/${month}/${year}`;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("pt-BR");
}

export function formatValidationDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatValidationDecision(decision: string | null | undefined): string {
  if (!decision) return "—";
  if (decision === "cortar") return "CORTAR";
  if (decision === "nao_cortar") return "NÃO CORTAR";
  if (decision === "inconclusivo") return "INCONCLUSIVO";
  return decision.toUpperCase();
}

export function formatValidationConfidence(confidence: string | null | undefined): string {
  if (!confidence) return "—";
  if (confidence === "high") return "Alta";
  if (confidence === "medium") return "Média";
  if (confidence === "low") return "Baixa";
  return confidence;
}
