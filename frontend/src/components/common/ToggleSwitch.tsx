interface ToggleSwitchProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

export function ToggleSwitch({
  enabled,
  onChange,
  disabled,
  ariaLabel
}: ToggleSwitchProps) {
  return (
    <button
      type="button"
      onClick={() => {
        if (!disabled) {
          onChange(!enabled);
        }
      }}
      disabled={disabled}
      aria-label={ariaLabel}
      className={`relative inline-flex h-5 w-9 items-center rounded-full border text-[0px] transition-colors ${
        enabled ? "border-sky-500 bg-sky-600" : "border-slate-600 bg-slate-800"
      } ${
        disabled
          ? "cursor-not-allowed opacity-60"
          : "cursor-pointer hover:border-sky-400"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full shadow transition-transform ${
          enabled ? "translate-x-4 bg-white" : "translate-x-1 bg-slate-400"
        }`}
      />
    </button>
  );
}
