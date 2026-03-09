export default function ChatPage(): JSX.Element {
  return (
    <div className="flex h-full min-w-0 flex-col gap-4">
      <header className="shrink-0 border-b border-slate-800 pb-4">
        <h1 className="text-xl font-semibold tracking-tight text-slate-50">
          Chat
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Interact with Septum&apos;s privacy-preserving assistant.
        </p>
      </header>
      <div className="min-h-0 flex-1 rounded-lg border border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-300">
        Chat UI coming soon.
      </div>
    </div>
  );
}

