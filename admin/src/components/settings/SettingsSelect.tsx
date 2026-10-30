import type { SelectHTMLAttributes } from "react";

export type SelectOption<T extends string | number> = { value: T; label: string };

type Props<T extends string | number> = Omit<SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange"> & {
  value: T | null | undefined;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
};

/**
 * 设置项下拉：选项固定，不手填。
 * 当前值不在选项里（历史配置或环境变量带来的值）时，作为「当前值」保留在最前面，避免保存时被悄悄改掉。
 * 数字选项按数字回传。
 */
export function SettingsSelect<T extends string | number>({ value, options, onChange, className, ...rest }: Props<T>) {
  const numeric = options.length > 0 && typeof options[0].value === "number";
  const current = value ?? (numeric ? 0 : "");
  const known = options.some((o) => String(o.value) === String(current));
  const list: SelectOption<T>[] =
    known || current === "" ? options : [{ value: current as T, label: `${current}（当前值）` }, ...options];
  return (
    <select
      {...rest}
      className={className ?? "settings-select"}
      value={String(current)}
      onChange={(e) => {
        const raw = e.target.value;
        onChange((numeric ? Number(raw) : raw) as T);
      }}
    >
      {list.map((o) => (
        <option key={String(o.value)} value={String(o.value)}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
