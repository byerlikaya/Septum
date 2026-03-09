export default function SettingsPage(): JSX.Element {
  return (
    <div className="flex h-full flex-col gap-4">
      <header className="flex items-center justify-between border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Settings
          </h1>
          <p className="text-sm text-muted-foreground">
            Configure LLM, privacy, and ingestion settings.
          </p>
        </div>
      </header>
      <div className="flex-1 rounded-lg border border-border bg-card/40 p-4 text-sm text-muted-foreground">
        Settings UI coming soon.
      </div>
    </div>
  );
}

