"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

const INVALID_MESSAGE = "E-mail ou senha inválidos.";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
      });
      if (!response.ok) {
        setError(response.status === 429 ? "Muitas tentativas. Aguarde e tente novamente." : INVALID_MESSAGE);
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("Não foi possível entrar agora. Tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">Motiva</div>
        <p className="login-eyebrow">Faixa Verde</p>
        <h1 id="login-title">Acesso à Beta</h1>
        <p className="login-description">Entre com suas credenciais para continuar.</p>
        <form onSubmit={submit} className="login-form">
          <label htmlFor="email">E-mail</label>
          <input id="email" name="email" type="email" autoComplete="username" required />
          <label htmlFor="password">Senha</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required />
          {error && <p className="login-error" role="alert">{error}</p>}
          <button type="submit" disabled={submitting}>{submitting ? "Entrando…" : "Entrar"}</button>
        </form>
      </section>
    </main>
  );
}
