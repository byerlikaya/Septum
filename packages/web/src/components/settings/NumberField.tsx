import { FieldHint } from "./FieldHint";
import { SavingIndicator } from "./SavingIndicator";

type NumberFieldProps = {
  label: string;
  description: string;
  value: number;
  onBlur: (rawValue: string) => void | Promise<void>;
  saving?: boolean;
};

export function NumberField({
  label,
  description,
  value,
  onBlur,
  saving
}: NumberFieldProps) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-slate-200">
        {label}
      </label>
      <input
        type="number"
        className="w-full rounded-md border border-border bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-50 outline-none ring-0 transition focus:border-sky-500"
        defaultValue={value}
        onBlur={async (event) => {
          await onBlur(event.target.value);
        }}
      />
      <FieldHint text={description} />
      {saving && <SavingIndicator />}
    </div>
  );
}
