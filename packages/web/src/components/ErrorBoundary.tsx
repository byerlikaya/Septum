"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { sendFrontendError } from "@/lib/api";

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  hasError: boolean;
  error: Error | null;
};

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    const route =
      typeof window !== "undefined" ? window.location.pathname : undefined;
    sendFrontendError({
      message: error.message,
      stack_trace: error.stack ?? errorInfo.componentStack ?? undefined,
      route,
      level: "ERROR",
      extra: { componentStack: errorInfo.componentStack ?? undefined }
    });
  }

  render(): ReactNode {
    if (this.state.hasError && this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-red-900/50 bg-red-950/30 p-8 text-red-200">
          <h2 className="text-lg font-semibold">Something went wrong</h2>
          <p className="text-sm opacity-90">{this.state.error.message}</p>
          <button
            type="button"
            onClick={() =>
              this.setState({ hasError: false, error: null })
            }
            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-100 hover:bg-slate-700"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
