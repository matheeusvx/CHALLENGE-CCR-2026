"use client";

import { useRef, useState } from "react";
import {
  AlertCircle,
  Building,
  Calendar,
  Check,
  Download,
  FileCheck,
  FileClock,
  Layers,
  Mail,
  MapPin,
  Pencil,
  Printer,
  ShieldCheck,
  Trash2,
  Upload,
  User,
  X,
} from "lucide-react";
import { useOperatorProfileStore } from "@/stores/operator-profile-store";
import { useHistoryStore } from "@/stores/history-store";
import {
  computeProductivityStats,
  downloadProductivityCsv,
  type ProductivityPeriod,
} from "@/lib/analytics/productivity";
import type { AppView } from "@/components/layout/app-sidebar";

function initialsOf(name: string) {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function AccountView({ onNavigate }: { onNavigate?: (view: AppView) => void }) {
  const profile = useOperatorProfileStore();
  const historyEntries = useHistoryStore((s) => s.entries);

  // Estados de edição de perfil
  const [isEditing, setIsEditing] = useState(false);
  const [nameInput, setNameInput] = useState(profile.name);
  const [emailInput, setEmailInput] = useState(profile.email);
  const [tempAvatar, setTempAvatar] = useState<string | null>(profile.avatarDataUrl);
  const [nameError, setNameError] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Estados de métricas e produtividade
  const [period, setPeriod] = useState<ProductivityPeriod>("all");
  const stats = computeProductivityStats(historyEntries, period);

  const handleStartEdit = () => {
    setNameInput(profile.name);
    setEmailInput(profile.email);
    setTempAvatar(profile.avatarDataUrl);
    setNameError(null);
    setEmailError(null);
    setFileError(null);
    setSuccessMessage(null);
    setIsEditing(true);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setFileError(null);
    setNameError(null);
    setEmailError(null);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const validTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!validTypes.includes(file.type)) {
      setFileError("Formato de imagem inválido. Use JPG, PNG ou WEBP.");
      return;
    }

    const maxSize = 2 * 1024 * 1024; // 2MB
    if (file.size > maxSize) {
      setFileError("A imagem deve ter no máximo 2 MB.");
      return;
    }

    setFileError(null);
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        setTempAvatar(reader.result);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleRemovePhoto = () => {
    setTempAvatar(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSaveProfile = (e: React.FormEvent) => {
    e.preventDefault();
    let hasError = false;

    const trimmedName = nameInput.trim();
    if (!trimmedName || trimmedName.length < 2) {
      setNameError("O nome deve ter pelo menos 2 caracteres.");
      hasError = true;
    } else if (trimmedName.length > 60) {
      setNameError("O nome não pode exceder 60 caracteres.");
      hasError = true;
    } else {
      setNameError(null);
    }

    const trimmedEmail = emailInput.trim();
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!trimmedEmail || !emailPattern.test(trimmedEmail)) {
      setEmailError("Informe um endereço de e-mail válido.");
      hasError = true;
    } else {
      setEmailError(null);
    }

    if (hasError) return;

    profile.updateProfile({ name: trimmedName, email: trimmedEmail });
    profile.setAvatar(tempAvatar);
    setIsEditing(false);
    setSuccessMessage("Perfil atualizado com sucesso.");
    setTimeout(() => setSuccessMessage(null), 4000);
  };

  const handlePrint = () => {
    if (typeof window !== "undefined") {
      window.print();
    }
  };

  const formatArea = (m2: number) => {
    if (m2 >= 10_000) {
      const ha = m2 / 10_000;
      return `${ha.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 })} ha`;
    }
    return `${Math.round(m2).toLocaleString("pt-BR")} m²`;
  };

  return (
    <section className="secondary-view account-view" aria-labelledby="secondary-view-account">
      <header className="account-view-header page-heading">
        <span className="page-heading-eyebrow">Operador do sistema</span>
        <h1 id="secondary-view-account" className="page-heading-title">Minha conta</h1>
        <p className="page-heading-description">Gerencie seu perfil e acompanhe sua atividade no Motiva Faixa Verde.</p>
      </header>

      {/* Mensagem de sucesso via aria-live */}
      <div className="sr-only" aria-live="polite">
        {successMessage}
      </div>

      {successMessage ? (
        <div className="account-toast success" role="status">
          <Check size={16} aria-hidden="true" />
          <span>{successMessage}</span>
        </div>
      ) : null}

      {/* Cartão de Perfil do Operador */}
      <section className="account-card profile-section" aria-labelledby="profile-heading">
        <div className="account-card-head">
          <User size={18} aria-hidden="true" />
          <div>
            <h2 id="profile-heading">Perfil do operador</h2>
            <span>Informações operacionais e credenciais locais</span>
          </div>
          {!isEditing ? (
            <button
              type="button"
              className="secondary-button edit-profile-btn"
              onClick={handleStartEdit}
            >
              <Pencil size={14} aria-hidden="true" />
              Editar perfil
            </button>
          ) : null}
        </div>

        {!isEditing ? (
          <div className="profile-display">
            <div className="profile-display-avatar">
              {profile.avatarDataUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={profile.avatarDataUrl} alt={profile.name} className="avatar-image-lg" />
              ) : (
                <div className="avatar-initials-lg" aria-hidden="true">
                  {initialsOf(profile.name)}
                </div>
              )}
            </div>

            <div className="profile-display-details">
              <div className="profile-names">
                <strong>{profile.name}</strong>
                <span className="profile-role-badge">{profile.role}</span>
              </div>
              <dl className="profile-meta-grid">
                <div>
                  <Building size={14} aria-hidden="true" />
                  <dt>Organização</dt>
                  <dd>{profile.organization}</dd>
                </div>
                <div>
                  <Mail size={14} aria-hidden="true" />
                  <dt>E-mail</dt>
                  <dd>{profile.email}</dd>
                </div>
              </dl>
            </div>
          </div>
        ) : (
          <form className="profile-edit-form" onSubmit={handleSaveProfile} noValidate data-testid="profile-edit-form">
            <div className="profile-avatar-edit-row">
              <div className="avatar-preview-container">
                {tempAvatar ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={tempAvatar} alt="Pré-visualização da foto" className="avatar-image-lg" />
                ) : (
                  <div className="avatar-initials-lg" aria-hidden="true">
                    {initialsOf(nameInput || profile.name)}
                  </div>
                )}
              </div>

              <div className="avatar-actions-col">
                <div className="avatar-buttons">
                  <button
                    type="button"
                    className="secondary-button upload-btn"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Upload size={14} aria-hidden="true" />
                    Alterar foto
                  </button>
                  {tempAvatar ? (
                    <button
                      type="button"
                      className="icon-button remove-photo-btn"
                      title="Remover foto"
                      aria-label="Remover foto"
                      onClick={handleRemovePhoto}
                    >
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="sr-only"
                  onChange={handleFileChange}
                />
                <span className="avatar-help-text">JPG, PNG ou WEBP até 2 MB.</span>
                {fileError ? <p className="field-error">{fileError}</p> : null}
              </div>
            </div>

            <div className="form-fields-grid">
              <div className="form-group">
                <label htmlFor="edit-name">Nome completo</label>
                <input
                  id="edit-name"
                  type="text"
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  className={nameError ? "input-error" : ""}
                  aria-invalid={Boolean(nameError)}
                  aria-describedby={nameError ? "name-error" : undefined}
                />
                {nameError ? (
                  <p id="name-error" className="field-error">
                    {nameError}
                  </p>
                ) : null}
              </div>

              <div className="form-group">
                <label htmlFor="edit-email">E-mail corporativo</label>
                <input
                  id="edit-email"
                  type="email"
                  value={emailInput}
                  onChange={(e) => setEmailInput(e.target.value)}
                  className={emailError ? "input-error" : ""}
                  aria-invalid={Boolean(emailError)}
                  aria-describedby={emailError ? "email-error" : undefined}
                />
                {emailError ? (
                  <p id="email-error" className="field-error">
                    {emailError}
                  </p>
                ) : null}
              </div>
            </div>

            <div className="form-actions-row">
              <button type="button" className="secondary-button" onClick={handleCancelEdit}>
                <X size={14} aria-hidden="true" />
                Cancelar
              </button>
              <button type="submit" className="primary-button">
                <Check size={14} aria-hidden="true" />
                Salvar alterações
              </button>
            </div>
          </form>
        )}
      </section>

      {/* Seção de Produtividade */}
      <section className="account-card productivity-section" aria-labelledby="productivity-heading">
        <div className="productivity-header">
          <div>
            <h2 id="productivity-heading">Produtividade operacional</h2>
            <span>Métricas reais consolidadas a partir do histórico deste dispositivo</span>
          </div>

          <div className="productivity-controls">
            {/* Filtros de período */}
            <div className="period-filter-group" role="radiogroup" aria-label="Período das métricas">
              <button
                type="button"
                role="radio"
                aria-checked={period === "7d"}
                className={`period-btn ${period === "7d" ? "active" : ""}`}
                onClick={() => setPeriod("7d")}
              >
                Últimos 7 dias
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={period === "30d"}
                className={`period-btn ${period === "30d" ? "active" : ""}`}
                onClick={() => setPeriod("30d")}
              >
                Últimos 30 dias
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={period === "all"}
                className={`period-btn ${period === "all" ? "active" : ""}`}
                onClick={() => setPeriod("all")}
              >
                Todo período
              </button>
            </div>

            {/* Ações de exportação */}
            {historyEntries.length > 0 ? (
              <div className="export-actions">
                <button
                  type="button"
                  className="secondary-button export-btn"
                  title="Exportar dados do período em formato CSV"
                  onClick={() => downloadProductivityCsv(historyEntries, period)}
                >
                  <Download size={14} aria-hidden="true" />
                  Baixar relatório
                </button>
                <button
                  type="button"
                  className="icon-button print-btn"
                  title="Imprimir relatório"
                  aria-label="Imprimir relatório"
                  onClick={handlePrint}
                >
                  <Printer size={15} aria-hidden="true" />
                </button>
              </div>
            ) : null}
          </div>
        </div>

        {/* Estado vazio quando não houver histórico registrado */}
        {historyEntries.length === 0 ? (
          <div className="account-empty-state">
            <div className="empty-icon-wrap" aria-hidden="true">
              <FileClock size={28} />
            </div>
            <h3>Você ainda não possui análises registradas neste navegador.</h3>
            <p>
              Desenhe uma área de interesse no mapa para executar sua primeira verificação de vegetação.
            </p>
            <button
              type="button"
              className="primary-button"
              onClick={() => onNavigate?.("analysis")}
            >
              Nova análise
            </button>
          </div>
        ) : (
          <div className="productivity-content">
            {/* 3 KPIs principais */}
            <div className="kpi-grid">
              <div className="kpi-card">
                <div className="kpi-icon-wrap" aria-hidden="true">
                  <FileCheck size={20} />
                </div>
                <div className="kpi-data">
                  <span className="kpi-label">Análises realizadas</span>
                  <strong className="kpi-value">{stats.totalAnalyses}</strong>
                  <span className="kpi-sub">
                    {period === "all"
                      ? `${stats.analysesLast7Days} nos últimos 7 dias · ${stats.analysesLast30Days} nos últimos 30 dias`
                      : period === "7d"
                      ? "Filtro: últimos 7 dias"
                      : "Filtro: últimos 30 dias"}
                  </span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon-wrap" aria-hidden="true">
                  <Layers size={20} />
                </div>
                <div className="kpi-data">
                  <span className="kpi-label">Área total analisada</span>
                  <strong className="kpi-value">{formatArea(stats.totalAreaM2)}</strong>
                  <span className="kpi-sub">
                    Média de {formatArea(stats.averageAreaM2)} por análise
                  </span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon-wrap" aria-hidden="true">
                  <MapPin size={20} />
                </div>
                <div className="kpi-data">
                  <span className="kpi-label">Rodovia mais analisada</span>
                  <strong className="kpi-value road-name-val">
                    {stats.mostAnalyzedRoad ?? "Sem dados suficientes"}
                  </strong>
                  <span className="kpi-sub">
                    {stats.mostAnalyzedRoad
                      ? `${stats.mostAnalyzedRoadCount} ${
                          stats.mostAnalyzedRoadCount === 1 ? "análise representativa" : "análises representativas"
                        }`
                      : "Nenhuma rodovia com referência espacial"}
                  </span>
                </div>
              </div>
            </div>

            {/* Linha dupla: Distribuição de Decisões e Rodovias */}
            <div className="productivity-details-grid">
              {/* Distribuição de Decisões */}
              <div className="productivity-subcard decisions-subcard">
                <div className="subcard-header">
                  <h3>Distribuição de recomendações</h3>
                  <span>Decisões registradas no período</span>
                </div>

                {stats.totalAnalyses > 0 ? (
                  <div className="decision-bar-container">
                    <div className="stacked-decision-bar" role="progressbar" aria-label="Distribuição de recomendações">
                      {stats.decisionPercentages.cortar > 0 ? (
                        <div
                          className="decision-segment cortar"
                          style={{ width: `${stats.decisionPercentages.cortar}%` }}
                          title={`Cortar: ${stats.decisions.cortar} (${stats.decisionPercentages.cortar}%)`}
                        />
                      ) : null}
                      {stats.decisionPercentages.nao_cortar > 0 ? (
                        <div
                          className="decision-segment nao-cortar"
                          style={{ width: `${stats.decisionPercentages.nao_cortar}%` }}
                          title={`Não cortar: ${stats.decisions.nao_cortar} (${stats.decisionPercentages.nao_cortar}%)`}
                        />
                      ) : null}
                      {stats.decisionPercentages.inconclusivo > 0 ? (
                        <div
                          className="decision-segment inconclusivo"
                          style={{ width: `${stats.decisionPercentages.inconclusivo}%` }}
                          title={`Inconclusivo: ${stats.decisions.inconclusivo} (${stats.decisionPercentages.inconclusivo}%)`}
                        />
                      ) : null}
                    </div>

                    <div className="decision-legend">
                      <div className="legend-item">
                        <span className="legend-dot cortar" aria-hidden="true" />
                        <span className="legend-name">Cortar</span>
                        <strong>{stats.decisions.cortar}</strong>
                        <span className="legend-pct">({stats.decisionPercentages.cortar}%)</span>
                      </div>
                      <div className="legend-item">
                        <span className="legend-dot nao-cortar" aria-hidden="true" />
                        <span className="legend-name">Não cortar</span>
                        <strong>{stats.decisions.nao_cortar}</strong>
                        <span className="legend-pct">({stats.decisionPercentages.nao_cortar}%)</span>
                      </div>
                      <div className="legend-item">
                        <span className="legend-dot inconclusivo" aria-hidden="true" />
                        <span className="legend-name">Inconclusivo</span>
                        <strong>{stats.decisions.inconclusivo}</strong>
                        <span className="legend-pct">({stats.decisionPercentages.inconclusivo}%)</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="no-period-data">Nenhuma análise registrada neste período selecionado.</p>
                )}
              </div>

              {/* Distribuição por Rodovia */}
              <div className="productivity-subcard roads-subcard">
                <div className="subcard-header">
                  <h3>Distribuição por rodovia</h3>
                  <span>Frequência das vias analisadas</span>
                </div>

                {stats.roadDistribution.length > 0 ? (
                  <div className="roads-list" role="list">
                    {stats.roadDistribution.slice(0, 5).map((r) => (
                      <div key={r.road} className="road-stat-row" role="listitem">
                        <div className="road-stat-info">
                          <span className="road-badge">{r.road}</span>
                          <span className="road-count">
                            {r.count} {r.count === 1 ? "análise" : "análises"}
                          </span>
                        </div>
                        <div className="road-stat-bar-wrap">
                          <div className="road-stat-bar" style={{ width: `${r.percentage}%` }} />
                        </div>
                        <span className="road-pct">{r.percentage}%</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="no-roads-box">
                    <AlertCircle size={16} aria-hidden="true" />
                    <p>Sem referências de rodovia (SP) identificadas nas zonas do período.</p>
                  </div>
                )}
              </div>
            </div>

            {/* Linha do tempo visual de atividade */}
            {stats.timeline.length > 0 ? (
              <div className="productivity-subcard timeline-subcard">
                <div className="subcard-header">
                  <h3>
                    <Calendar size={15} aria-hidden="true" />
                    Análises ao longo do tempo
                  </h3>
                  <span>Atividade operacional diária</span>
                </div>

                <div className="timeline-bars-wrap">
                  {(() => {
                    const maxCount = Math.max(...stats.timeline.map((t) => t.count), 1);
                    return stats.timeline.map((item) => {
                      const heightPct = Math.max(12, Math.round((item.count / maxCount) * 100));
                      return (
                        <div
                          key={item.date}
                          className="timeline-bar-col"
                          title={`${item.date}: ${item.count} ${item.count === 1 ? "análise" : "análises"} (${formatArea(item.areaM2)})`}
                        >
                          <div className="timeline-bar-fill-wrap">
                            <span className="timeline-count-badge">{item.count}</span>
                            <div className="timeline-bar-fill" style={{ height: `${heightPct}%` }} />
                          </div>
                          <span className="timeline-label">{item.label}</span>
                        </div>
                      );
                    });
                  })()}
                </div>
              </div>
            ) : null}

            {/* Nota discreta exigida */}
            <p className="productivity-disclaimer">
              <ShieldCheck size={14} aria-hidden="true" />
              Estatísticas calculadas a partir do histórico salvo neste navegador.
            </p>
          </div>
        )}
      </section>
    </section>
  );
}
