export default function DocumentsPage(): JSX.Element {
  return (
    <div className="flex h-full flex-col gap-4">
      <header className="flex items-center justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Documents
          </h1>
          <p className="text-sm text-muted-foreground">
            Upload, inspect, and manage ingested documents.
          </p>
        </div>
      </header>
      <div className="flex-1 rounded-lg border border-border bg-card/40 p-4 text-sm text-muted-foreground">
        Documents UI coming soon.
      </div>
    </div>
  );
}

