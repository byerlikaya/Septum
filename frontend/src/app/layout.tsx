import { ReactNode } from "react";
import "./globals.css";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { GlobalErrorListener } from "@/components/GlobalErrorListener";
import { SetupGuard } from "@/components/common/SetupGuard";
import { AppShell } from "@/components/layout/AppShell";
import { LanguageProvider } from "@/lib/language";

type RootLayoutProps = {
  children: ReactNode;
};

import type { Viewport } from "next";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export const metadata = {
  title: "Septum",
  description: "Privacy-first AI middleware"
} as const;

export default function RootLayout(props: RootLayoutProps) {
  const { children } = props;

  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="h-screen bg-slate-950 text-foreground antialiased overflow-auto md:overflow-hidden">
        <LanguageProvider>
          <ErrorBoundary>
            <GlobalErrorListener />
            <SetupGuard>
              <AppShell>{children}</AppShell>
            </SetupGuard>
          </ErrorBoundary>
        </LanguageProvider>
      </body>
    </html>
  );
}
