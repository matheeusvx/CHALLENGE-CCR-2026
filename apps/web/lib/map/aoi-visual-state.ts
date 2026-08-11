export type AoiVisualStateId =
  | "editing"
  | "pending_validation"
  | "valid"
  | "invalid"
  | "cut"
  | "no_cut"
  | "inconclusive";

export type AoiRecommendation = "cortar" | "nao_cortar" | "inconclusivo";

export type AoiVisualState = {
  id: AoiVisualStateId;
  label: string;
  color: string;
  fillOpacity: number;
  dashed: boolean;
};

const AOI_VISUAL_STATES: Record<AoiVisualStateId, AoiVisualState> = {
  editing: { id: "editing", label: "Área em edição", color: "#7c3aed", fillOpacity: 0.25, dashed: false },
  pending_validation: { id: "pending_validation", label: "Aguardando validação", color: "#c56a16", fillOpacity: 0.2, dashed: false },
  valid: { id: "valid", label: "Área validada", color: "#27865b", fillOpacity: 0.18, dashed: false },
  invalid: { id: "invalid", label: "Área inválida", color: "#c43d46", fillOpacity: 0.16, dashed: true },
  cut: { id: "cut", label: "Cortar", color: "#d44d2f", fillOpacity: 0.2, dashed: false },
  no_cut: { id: "no_cut", label: "Não cortar", color: "#27865b", fillOpacity: 0.18, dashed: false },
  inconclusive: { id: "inconclusive", label: "Inconclusivo", color: "#a77919", fillOpacity: 0.17, dashed: true },
};

type VisualStateInput = {
  editing: boolean;
  dirty: boolean;
  validation: "valid" | "invalid" | null;
  recommendation?: AoiRecommendation;
};

export function getAoiVisualState(input: VisualStateInput): AoiVisualState {
  if (input.editing) return AOI_VISUAL_STATES.editing;
  if (input.dirty) return AOI_VISUAL_STATES.pending_validation;
  if (input.validation === "invalid") return AOI_VISUAL_STATES.invalid;
  if (input.recommendation === "cortar") return AOI_VISUAL_STATES.cut;
  if (input.recommendation === "nao_cortar") return AOI_VISUAL_STATES.no_cut;
  if (input.recommendation === "inconclusivo") return AOI_VISUAL_STATES.inconclusive;
  if (input.validation === "valid") return AOI_VISUAL_STATES.valid;
  return AOI_VISUAL_STATES.pending_validation;
}
