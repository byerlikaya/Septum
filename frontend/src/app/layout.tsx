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
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="min-h-screen bg-slate-950 text-foreground antialiased">
        <div className="flex min-h-screen min-w-0">
          <Sidebar />
          <main className="min-w-0 flex-1 bg-slate-950">
            <div className="h-full min-w-0 w-full px-4 py-5 sm:px-6 lg:px-8">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}

