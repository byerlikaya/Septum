interface ErrorAlertProps {
  message: string;
  className?: string;
}

export function ErrorAlert({ message, className }: ErrorAlertProps) {
  return (
    <div
      className={`rounded-md border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200 ${className ?? ""}`}
    >
      {message}
    </div>
  );
}
