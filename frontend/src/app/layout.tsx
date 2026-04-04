import { ReactNode } from "react";
import "./globals.css";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { GlobalErrorListener } from "@/components/GlobalErrorListener";
import { Sidebar } from "@/components/layout/Sidebar";
import { SetupGuard } from "@/components/common/SetupGuard";
import { LanguageProvider } from "@/lib/language";

type RootLayoutProps = {
  children: ReactNode;
};

export const metadata = {
  title: "Septum",
  description: "Privacy-first AI middleware"
} as const;

export default function RootLayout(props: RootLayoutProps) {
  const { children } = props;

  return (
    <html lang="en" className="dark suppressHydrationWarning">
      <body className="h-screen overflow-hidden bg-slate-950 text-foreground antialiased">
        <LanguageProvider>
          <ErrorBoundary>
            <GlobalErrorListener />
            <SetupGuard>
              <div className="flex h-full min-w-0 flex-col md:flex-row">
                <Sidebar />
                <main className="min-h-0 min-w-0 flex-1 overflow-hidden bg-slate-950">
                  <div className="flex h-full min-h-0 min-w-0 w-full flex-col overflow-hidden px-4 py-5 sm:px-6 lg:px-8">
                    {children}
                  </div>
                </main>
              </div>
            </SetupGuard>
          </ErrorBoundary>
        </LanguageProvider>
      </body>
    </html>
  );
}

