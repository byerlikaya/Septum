import { ToggleSwitch } from "@/components/common/ToggleSwitch";
import { SavingIndicator } from "./SavingIndicator";

type ToggleFieldProps = {
  label: string;
  description: string;
  checked: boolean;
  onToggle: (value: boolean) => void | Promise<void>;
  saving?: boolean;
};

export function ToggleField({
  label,
  description,
  checked,
  onToggle,
  saving
}: ToggleFieldProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-slate-200">{label}</p>
          <p className="text-[11px] text-slate-400">{description}</p>
        </div>
        <ToggleSwitch
          enabled={checked}
          onChange={(value) => {
            void onToggle(value);
          }}
        />
      </div>
      {saving && <SavingIndicator />}
    </div>
  );
}
