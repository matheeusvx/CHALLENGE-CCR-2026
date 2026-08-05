import { create } from "zustand";

type FormState = {
  geometryText: string;
  startDate: string;
  endDate: string;
  maxCloudCover: number;
  maxScenes: number;
  minValidPixelPercentage: number;
  dailyAggregation: "best" | "median" | "none";
  geometryValidated: boolean;
  setField: <K extends keyof Omit<FormState, "setField">>(key: K, value: FormState[K]) => void;
};

export const useAnalysisStore = create<FormState>((set) => ({
  geometryText: "",
  startDate: "2026-05-01",
  endDate: "2026-08-04",
  maxCloudCover: 30,
  maxScenes: 12,
  minValidPixelPercentage: 70,
  dailyAggregation: "best",
  geometryValidated: false,
  setField: (key, value) =>
    set(() => ({
      [key]: value,
      ...(key === "geometryText" ? { geometryValidated: false } : {}),
    }) as Partial<FormState>),
}));
