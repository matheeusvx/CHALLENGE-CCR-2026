import type { Metadata } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import type { ReactNode } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const plex = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Motiva Faixa Verde",
  description: "Monitoramento da vegetação lateral rodoviária com imagens Sentinel-2.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  // O tema inicia em escuro (padrão do produto). O ThemeProvider ajusta para a
  // preferência salva do usuário (claro/escuro/sistema) após a hidratação.
  return (
    <html lang="pt-BR" data-theme="dark" suppressHydrationWarning>
      <body className={`${inter.variable} ${plex.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
