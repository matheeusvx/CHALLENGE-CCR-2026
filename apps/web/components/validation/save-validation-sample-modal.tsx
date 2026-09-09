"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, FlaskConical, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { createValidationSample } from "@/lib/api/validation";
import type {
  MaintenanceTruth,
  ValidationSampleCreate,
  ValidationSource,
  VegetationClass,
} from "@/lib/schemas/validation";
import {
  MAINTENANCE_TRUTH_LABELS,
  VALIDATION_SOURCE_LABELS,
  VEGETATION_CLASS_FORM_OPTIONS,
} from "@/lib/utils/validation";

export interface SaveValidationSampleModalProps {
  analysisId: string;
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function SaveValidationSampleModal({
  analysisId,
  isOpen,
  onClose,
  onSaved,
}: SaveValidationSampleModalProps) {
  const queryClient = useQueryClient();
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);

  const today = new Date().toISOString().split("T")[0];

  const [vegetationClass, setVegetationClass] = useState<VegetationClass>("low_grass");
  const [maintenanceTruth, setMaintenanceTruth] = useState<MaintenanceTruth>("no_cut");
  const [validationSource, setValidationSource] = useState<ValidationSource>("visual_inspection");
  const [referenceDate, setReferenceDate] = useState<string>(today);
  const [notes, setNotes] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleClose = useCallback(() => {
    setErrorMessage(null);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;

    previouslyFocusedElementRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocusedElementRef.current?.focus();
    };
  }, [isOpen, handleClose]);

  const mutation = useMutation({
    mutationFn: (payload: ValidationSampleCreate) => createValidationSample(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["validation-summary"] });
      queryClient.invalidateQueries({ queryKey: ["validation-samples"] });
      onSaved();
      onClose();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        if (err.status === 409 || err.code === "VALIDATION_SAMPLE_EXISTS") {
          setErrorMessage("Esta análise já foi registrada como amostra de validação.");
          return;
        }
        if (err.status === 404 || err.code === "ANALYSIS_NOT_AVAILABLE") {
          setErrorMessage(
            "Esta análise não está mais disponível para registro. Execute a análise novamente e salve a nova execução.",
          );
          return;
        }
      }
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "Não foi possível salvar a amostra de validação. Tente novamente.",
      );
    },
  });

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const payload: ValidationSampleCreate = {
      analysis_id: analysisId,
      vegetation_class: vegetationClass,
      maintenance_truth: maintenanceTruth,
      validation_source: validationSource,
      reference_date: referenceDate,
      notes: notes.trim() ? notes.trim() : null,
    };

    mutation.mutate(payload);
  };

  return (
    <div
      className="validation-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !mutation.isPending) onClose();
      }}
      role="presentation"
    >
      <div
        className="validation-create-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="save-validation-title"
      >
        <div className="validation-modal-head">
          <div className="title-group">
            <span className="experimental-badge">EXPERIMENTAL</span>
            <h2 id="save-validation-title">Salvar amostra de validação</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="icon-action close-button"
            onClick={onClose}
            disabled={mutation.isPending}
            aria-label="Fechar modal"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <p className="validation-modal-desc">
          Classifique manualmente o estado observado da área. Essas informações serão usadas apenas
          para avaliar os sensores e não alteram a recomendação atual.
        </p>

        {errorMessage ? (
          <div className="validation-form-error" role="alert">
            <AlertCircle size={18} aria-hidden="true" />
            <p>{errorMessage}</p>
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="validation-create-form">
          <div className="form-group">
            <label htmlFor="vegetation-class-select">
              Tipo de vegetação <span className="req">*</span>
            </label>
            <select
              id="vegetation-class-select"
              value={vegetationClass}
              onChange={(e) => setVegetationClass(e.target.value as VegetationClass)}
              disabled={mutation.isPending}
              required
            >
              {VEGETATION_CLASS_FORM_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="maintenance-truth-select">
              Necessidade real de manutenção <span className="req">*</span>
            </label>
            <select
              id="maintenance-truth-select"
              value={maintenanceTruth}
              onChange={(e) => setMaintenanceTruth(e.target.value as MaintenanceTruth)}
              disabled={mutation.isPending}
              required
            >
              {(Object.keys(MAINTENANCE_TRUTH_LABELS) as MaintenanceTruth[]).map((key) => (
                <option key={key} value={key}>
                  {MAINTENANCE_TRUTH_LABELS[key]}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="validation-source-select">
              Fonte da validação <span className="req">*</span>
            </label>
            <select
              id="validation-source-select"
              value={validationSource}
              onChange={(e) => setValidationSource(e.target.value as ValidationSource)}
              disabled={mutation.isPending}
              required
            >
              {(Object.keys(VALIDATION_SOURCE_LABELS) as ValidationSource[]).map((key) => (
                <option key={key} value={key}>
                  {VALIDATION_SOURCE_LABELS[key]}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="reference-date-input">
              Data da referência <span className="req">*</span>
            </label>
            <input
              type="date"
              id="reference-date-input"
              value={referenceDate}
              onChange={(e) => setReferenceDate(e.target.value)}
              disabled={mutation.isPending}
              required
            />
          </div>

          <div className="form-group">
            <div className="label-with-counter">
              <label htmlFor="notes-textarea">Observações (opcional)</label>
              <span className="char-counter">{notes.length} / 1000</span>
            </div>
            <textarea
              id="notes-textarea"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={1000}
              rows={3}
              placeholder="Descreva detalhes observados em campo ou na imagem de referência..."
              disabled={mutation.isPending}
            />
          </div>

          <div className="validation-modal-actions">
            <button
              type="button"
              className="quiet-action"
              onClick={onClose}
              disabled={mutation.isPending}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={mutation.isPending}
            >
              {mutation.isPending ? (
                <>
                  <Loader2 className="spinner" size={16} aria-hidden="true" />
                  Salvando...
                </>
              ) : (
                <>
                  <FlaskConical size={16} aria-hidden="true" />
                  Salvar amostra
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
