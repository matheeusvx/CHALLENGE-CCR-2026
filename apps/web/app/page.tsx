"use client";

import { Satellite, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { AnalysisForm } from "@/components/analysis/analysis-form";
import { AnalysisResult } from "@/components/analysis/analysis-result";
import { AppShell } from "@/components/layout/app-shell";
import type { AnalysisResponse as AnalysisResponseType } from "@/lib/schemas/analyses";

export default function Home() {
  const [result, setResult] = useState<AnalysisResponseType>();
  return (
    <AppShell>
      <div className="workspace-heading"><div><span>OPERACOES / VEGETACAO</span><h1>Motiva Vegetation Intelligence</h1><p>Monitoramento espectral de faixas laterais rodoviarias com Sentinel-2 L2A.</p></div><div className="workspace-badge"><Satellite size={19} /><span>Fonte ativa<strong>Microsoft Planetary Computer</strong></span></div></div>
      <section className="intro-band"><ShieldCheck size={23} /><div><h2>Analise experimental baseada em comportamento historico local</h2><p>Delimite uma area predominantemente gramada. A plataforma avalia qualidade, consolida observacoes e aplica as regras explicaveis do motor Python existente.</p></div></section>
      <AnalysisForm onResult={setResult} />
      {result ? <AnalysisResult result={result} /> : <div className="results-empty"><Satellite size={28} /><h2>Nenhuma analise executada</h2><p>Valide uma area para habilitar a execucao e visualizar os indicadores.</p></div>}
    </AppShell>
  );
}
