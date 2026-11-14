import { useEffect, useId, useRef, useState } from "react";
import { Check, LoaderCircle, Search, UserRound, X } from "lucide-react";
import { api, type AdminUserRow, type PageMeta } from "@/api/client";
import { formatAccountId, parseAccountIdQuery } from "@/lib/admin-account";
import { cn } from "@/lib/utils";
import "@/styles/admin-controls.css";

type ListRes = { items: AdminUserRow[]; meta: PageMeta };

type AdminUserSearchSelectProps = {
  value: number | null;
  onChange: (userId: number | null, user?: AdminUserRow | null) => void;
  placeholder?: string;
  className?: string;
};

/** 远程搜索用户（账号 ID + 邮箱） */
export function AdminUserSearchSelect({
  value,
  onChange,
  placeholder = "搜索用户邮箱 / 账号 ID",
  className,
}: AdminUserSearchSelectProps) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<AdminUserRow[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [selectedLabel, setSelectedLabel] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!value) {
      setSelectedLabel("");
      return;
    }
    const controller = new AbortController();
    setSelectedLabel(`ID：${formatAccountId(value)}`);
    void api<ListRes>(`/api/admin/users?page=1&page_size=10&q=${encodeURIComponent(String(value))}`, { signal: controller.signal })
      .then((res) => {
        const hit = res.items.find((user) => user.id === value);
        if (!controller.signal.aborted && hit) setSelectedLabel(`${hit.email} · ID：${formatAccountId(hit.id)}`);
      }).catch(() => { /* The account ID remains a useful fallback if loading fails. */ });
    return () => controller.abort();
  }, [value]);

  useEffect(() => {
    setOptions([]);
    setActiveIndex(-1);
    setError(false);
    const q = query.trim();
    if (!open || !q) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const accountId = parseAccountIdQuery(q);
        const searchQ = accountId != null ? String(accountId) : q;
        const res = await api<ListRes>(
          `/api/admin/users?page=1&page_size=10&q=${encodeURIComponent(searchQ)}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setOptions(res.items);
        setActiveIndex(res.items.length ? 0 : -1);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 260);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, open]);

  useEffect(() => {
    if (activeIndex >= 0) document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, listId]);

  function choose(user: AdminUserRow) {
    onChange(user.id, user);
    setSelectedLabel(`${user.email} · ID：${formatAccountId(user.id)}`);
    setQuery("");
    setOpen(false);
  }

  return (
    <div className={cn("admin-user-search admin-user-combobox", className)} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    }}>
      <UserRound size={16} className="admin-user-combobox-icon" aria-hidden="true" />
      <input
        ref={inputRef}
        className="admin-input"
        role="combobox"
        aria-label={placeholder}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        autoComplete="off"
        placeholder={value ? selectedLabel || placeholder : placeholder}
        value={open ? query : value ? selectedLabel : query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            setActiveIndex((current) => {
              if (!options.length) return -1;
              if (current < 0) return event.key === "ArrowDown" ? 0 : options.length - 1;
              return (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
            });
          } else if (event.key === "Enter" && open && activeIndex >= 0 && options[activeIndex]) {
            event.preventDefault();
            choose(options[activeIndex]);
          } else if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          } else if (event.key === "Tab") setOpen(false);
        }}
      />
      {value || query ? (
        <button
          type="button"
          className="admin-user-search-clear admin-control-clear"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            onChange(null, null);
            setQuery("");
            setSelectedLabel("");
            setOpen(true);
            inputRef.current?.focus();
          }}
          aria-label="清除用户"
        >
          <X size={13} />
        </button>
      ) : null}
      {open ? (
        <div className="admin-user-search-dropdown admin-user-combobox-popover">
          <div className="admin-user-combobox-heading"><Search size={13} aria-hidden="true" /><span>{query.trim() ? "匹配用户" : "查找用户"}</span>{options.length ? <span className="admin-user-combobox-count">{options.length} 位</span> : null}</div>
          <div id={listId} role="listbox" aria-label="搜索用户" className="admin-user-combobox-list" aria-busy={loading}>
          {loading ? <div className="admin-user-search-empty" role="status"><LoaderCircle size={17} className="admin-control-spinner" />正在查找用户…</div> : null}
          {!loading && options.length === 0 ? (
            <div className="admin-user-search-empty" role="status">{error ? "搜索失败，请重新输入后重试" : query.trim() ? "没有找到匹配用户" : "输入邮箱、昵称或账号 ID 搜索"}</div>
          ) : null}
          {options.map((u, index) => (
            <button
              key={u.id}
              type="button"
              id={`${listId}-${index}`}
              role="option"
              aria-selected={value === u.id}
              tabIndex={-1}
              className={cn("admin-user-search-option", activeIndex === index && "is-highlighted")}
              onPointerMove={() => setActiveIndex(index)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(u)}
            >
              <span className="admin-user-option-avatar">{(u.nickname || u.email).slice(0, 1).toUpperCase()}</span>
              <span className="admin-user-option-copy"><span className="admin-user-option-email">{u.email}</span>
              <span className="admin-user-option-meta">
                ID：{formatAccountId(u.id)}
                {u.nickname ? ` · ${u.nickname}` : ""}
              </span>
              </span>
              {value === u.id ? <Check size={15} className="admin-user-option-check" aria-hidden="true" /> : null}
            </button>
          ))}
          </div>
          <div className="admin-user-combobox-footer"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>Enter</kbd> 确认</span></div>
        </div>
      ) : null}
    </div>
  );
}
