import {
  validationSampleCreateSchema,
  validationSampleDetailSchema,
  validationSampleListSchema,
  validationSummarySchema,
  type MaintenanceTruth,
  type ValidationSampleCreate,
  type ValidationSampleDetail,
  type ValidationSampleList,
  type ValidationSource,
  type ValidationSummary,
  type VegetationClass,
} from "@/lib/schemas/validation";
import { apiRequest, toApiUrl } from "./client";

export type ListValidationSamplesParams = {
  vegetation_class?: VegetationClass | "" | null;
  maintenance_truth?: MaintenanceTruth | "" | null;
  validation_source?: ValidationSource | "" | null;
  limit?: number;
  offset?: number;
};

export const getValidationSummary = (): Promise<ValidationSummary> =>
  apiRequest("/api/validation-summary", validationSummarySchema);

export const getValidationSamples = (
  params?: ListValidationSamplesParams,
): Promise<ValidationSampleList> => {
  const query = new URLSearchParams();
  if (params?.vegetation_class) {
    query.set("vegetation_class", params.vegetation_class);
  }
  if (params?.maintenance_truth) {
    query.set("maintenance_truth", params.maintenance_truth);
  }
  if (params?.validation_source) {
    query.set("validation_source", params.validation_source);
  }
  if (typeof params?.limit === "number") {
    query.set("limit", String(params.limit));
  }
  if (typeof params?.offset === "number") {
    query.set("offset", String(params.offset));
  }

  const qs = query.toString();
  const path = qs ? `/api/validation-samples?${qs}` : "/api/validation-samples";
  return apiRequest(path, validationSampleListSchema);
};

export const getValidationSample = (sampleId: string): Promise<ValidationSampleDetail> =>
  apiRequest(`/api/validation-samples/${sampleId}`, validationSampleDetailSchema);

export const createValidationSample = (
  payload: ValidationSampleCreate,
): Promise<ValidationSampleDetail> => {
  const validatedPayload = validationSampleCreateSchema.parse(payload);
  return apiRequest("/api/validation-samples", validationSampleDetailSchema, {
    method: "POST",
    body: JSON.stringify(validatedPayload),
  });
};

export const getValidationExportUrl = (): string => toApiUrl("/api/validation-export");
