"use client";

interface BlockingLoaderProps {
  visible: boolean;
  label: string;
}

export function BlockingLoader({ visible, label }: BlockingLoaderProps): JSX.Element | null {
  if (!visible) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70">
      <div className="flex flex-col items-center gap-3 rounded-lg px-6 py-4 text-center">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
        <p className="text-sm font-medium text-slate-100">{label}</p>
      </div>
    </div>
  );
}

