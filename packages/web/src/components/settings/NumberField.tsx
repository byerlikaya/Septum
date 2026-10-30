import { useEffect, useRef } from "react";
import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";

type NumberFieldProps = {
  label: string;
  description: string;
  value: number;
  onBlur: (rawValue: string) => void | Promise<void>;
  saving?: boolean;
  min?: number;
  max?: number;
};

export function NumberField({
  label,
  description,
  value,
  onBlur,
  saving,
  min,
  max
}: Readonly<NumberFieldProps>) {
  const inputRef = useRef<HTMLInputElement>(null);

  // The input is uncontrolled; follow the value prop when the form changes it
  // (a clamped overlap, or a field reverted after a failed save).
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.value = String(value);
    }
  }, [value]);

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-slate-200">
        {label}
      </label>
      <input
        ref={inputRef}
        type="number"
        min={min}
        max={max}
        className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-hidden ring-0 transition focus:border-sky-500"
        defaultValue={value}
        onBlur={async (event) => {
          const input = event.target;
          const parsed = Number.parseInt(input.value, 10);
          if (!Number.isNaN(parsed)) {
            const clamped = Math.min(
              Math.max(parsed, min ?? Number.NEGATIVE_INFINITY),
              max ?? Number.POSITIVE_INFINITY
            );
            if (clamped !== parsed) {
              input.value = String(clamped);
            }
          }
          await onBlur(input.value);
        }}
      />
      <FieldHint text={description} />
      {saving && <SavingIndicator />}
    </div>
  );
}
