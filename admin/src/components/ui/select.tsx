import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import "@/styles/admin-controls.css";

type OptionElement = React.ReactElement<React.ComponentProps<"option">>;
type OptionRow = { value: string; label: React.ReactNode; text: string; disabled: boolean; group?: string };

function optionRows(children: React.ReactNode, group?: string): OptionRow[] {
  return React.Children.toArray(children).flatMap((child): OptionRow[] => {
    if (!React.isValidElement(child)) return [];
    if (child.type === "option") {
      const option = child as OptionElement;
      const label = option.props.children;
      const text = React.Children.toArray(label).join("");
      return [{ value: String(option.props.value ?? text), label, text, disabled: Boolean(option.props.disabled), group }];
    }
    const container = child as React.ReactElement<{ children?: React.ReactNode; label?: string; disabled?: boolean }>;
    return optionRows(container.props.children, child.type === "optgroup" ? container.props.label : group)
      .map((row) => ({ ...row, disabled: row.disabled || Boolean(container.props.disabled) }));
  });
}

/** Keeps the existing select/option API while using an accessible, unclipped menu. */
export function Select({ className, children, value, defaultValue, onChange, ref, ...props }: React.ComponentProps<"select">) {
  const nativeRef = React.useRef<HTMLSelectElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const searchRef = React.useRef<HTMLInputElement>(null);
  const contentRef = React.useRef<HTMLDivElement>(null);
  React.useImperativeHandle(ref, () => nativeRef.current as HTMLSelectElement);
  const rows = optionRows(children);
  const [internalValue, setInternalValue] = React.useState(() => String(defaultValue ?? rows.find((row) => !row.disabled)?.value ?? ""));
  const selectedValue = String(value ?? internalValue);
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState("");
  const searchable = rows.length > 8;
  const visibleRows = search.trim() ? rows.filter((row) => `${row.text} ${row.value} ${row.group ?? ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())) : rows;
  const emptyValue = `__admin_empty_${React.useId()}`;
  const encode = (next: string) => next === "" ? emptyValue : next;

  // Radix focuses an option once positioned; focus the search after that work has settled.
  React.useEffect(() => {
    if (!open || !searchable) return;
    const frame = requestAnimationFrame(() => searchRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [open, searchable, search]);

  function changeValue(next: string) {
    setInternalValue(next);
    const native = nativeRef.current;
    if (native) {
      native.value = next;
      native.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  if (props.multiple || (props.size ?? 0) > 1) {
    return <select {...props} ref={nativeRef} value={value} defaultValue={defaultValue} onChange={onChange} className={cn("admin-control admin-select", className)}>{children}</select>;
  }

  return (
    <>
      <select
        {...props}
        id={undefined}
        ref={nativeRef}
        value={selectedValue}
        onChange={onChange}
        tabIndex={-1}
        aria-hidden="true"
        style={{ display: "none" }}
        onInvalid={(event) => {
          props.onInvalid?.(event);
          event.preventDefault();
          triggerRef.current?.focus();
        }}
      >
        {children}
      </select>
      <SelectPrimitive.Root
        value={encode(selectedValue)} disabled={props.disabled} form={props.form}
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setSearch("");
        }}
        onValueChange={(encoded) => changeValue(encoded === emptyValue ? "" : encoded)}
      >
        <SelectPrimitive.Trigger
          ref={triggerRef}
          id={props.id} className={cn("admin-control admin-select admin-select-trigger", className)} title={props.title}
          aria-label={props["aria-label"] ?? (props.id ? undefined : rows.find((row) => row.value === selectedValue)?.text ?? "选择选项")}
          aria-labelledby={props["aria-labelledby"]} aria-describedby={props["aria-describedby"]} aria-invalid={props["aria-invalid"]}
          aria-required={props.required}
          tabIndex={props.tabIndex} autoFocus={props.autoFocus} style={props.style}
        >
          <SelectPrimitive.Value>{rows.find((row) => row.value === selectedValue)?.label ?? "请选择"}</SelectPrimitive.Value>
          <SelectPrimitive.Icon className="admin-select-chevron"><ChevronDown size={15} /></SelectPrimitive.Icon>
        </SelectPrimitive.Trigger>
        <SelectPrimitive.Portal>
          <SelectPrimitive.Content ref={contentRef} className="admin-select-popover" position="popper" sideOffset={6} collisionPadding={12}>
            {searchable ? <div className="admin-select-search-wrap">
              <Search size={14} aria-hidden="true" />
              <input ref={searchRef} className="admin-select-search-input" aria-label="搜索选项" placeholder="搜索选项…" value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") return;
                  event.stopPropagation();
                  if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
                  if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Tab") {
                    event.preventDefault();
                    const items = contentRef.current?.querySelectorAll<HTMLElement>('[role="option"]:not([data-disabled])');
                    const item = event.key === "ArrowUp" ? items?.[items.length - 1] : items?.[0];
                    item?.focus();
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    const first = visibleRows.find((row) => !row.disabled);
                    if (first) { changeValue(first.value); setOpen(false); setSearch(""); }
                  }
                }}
              />
              {search ? <button type="button" aria-label="清除选项搜索" className="admin-select-search-clear" onPointerDown={(event) => event.preventDefault()} onClick={() => { setSearch(""); searchRef.current?.focus(); }}><X size={13} /></button> : null}
            </div> : null}
            <SelectPrimitive.ScrollUpButton className="admin-select-scroll"><ChevronUp size={14} /></SelectPrimitive.ScrollUpButton>
            <SelectPrimitive.Viewport className="admin-select-viewport">
              {visibleRows.map((row, index) => (
                <React.Fragment key={row.value}>
                  {row.group && visibleRows[index - 1]?.group !== row.group ? <div className="admin-select-group">{row.group}</div> : null}
                  <SelectPrimitive.Item className="admin-select-item" value={encode(row.value)} disabled={row.disabled} textValue={row.text}>
                    <SelectPrimitive.ItemText>{row.label}</SelectPrimitive.ItemText>
                    <SelectPrimitive.ItemIndicator className="admin-select-check"><Check size={15} /></SelectPrimitive.ItemIndicator>
                  </SelectPrimitive.Item>
                </React.Fragment>
              ))}
              {visibleRows.length === 0 ? <div className="admin-select-empty">{search ? "没有匹配的选项" : "暂无可选项"}</div> : null}
            </SelectPrimitive.Viewport>
            <SelectPrimitive.ScrollDownButton className="admin-select-scroll"><ChevronDown size={14} /></SelectPrimitive.ScrollDownButton>
          </SelectPrimitive.Content>
        </SelectPrimitive.Portal>
      </SelectPrimitive.Root>
    </>
  );
}

// 管理端统一多行文本
export function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return <textarea className={cn("admin-control admin-textarea", className)} {...props} />;
}
