import { ReactNode } from "react";
import "./globals.css";
import { Sidebar } from "@/components/layout/Sidebar";

type RootLayoutProps = {
  children: ReactNode;
};

export const metadata = {
  title: "Septum",
  description: "Privacy-first AI middleware"
} as const;

export default function RootLayout(
  props: RootLayoutProps
): JSX.Element {
  const { children } = props;

  return (
    <html lang="en" suppressHydrationWarning>
      <body className="bg-background text-foreground antialiased">
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 bg-slate-950/95">
            <div className="mx-auto h-full max-w-6xl px-6 py-6">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}

