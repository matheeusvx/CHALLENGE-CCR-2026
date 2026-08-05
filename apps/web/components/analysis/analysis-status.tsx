import { AlertCircle, LoaderCircle } from "lucide-react";

export function AnalysisStatus({ loading, error }: { loading: boolean; error?: string }) {
  if (loading) {
    return <div className="analysis-status loading" role="status"><LoaderCircle className="spin" size={18} />Processando cenas e indicadores espectrais...</div>;
  }
  if (error) {
    return <div className="analysis-status error" role="alert"><AlertCircle size={18} /><span><strong>Falha na operacao</strong>{error}</span></div>;
  }
  return null;
}
