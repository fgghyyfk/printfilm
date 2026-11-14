import { ImageOff, Loader2, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AdminTemplate } from "@/api/client";
import { AdminField } from "@/components/admin/AdminField";
import { AdminModal } from "@/components/admin/AdminModal";
import { AdminSelect } from "@/components/admin/AdminSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { validateTemplateDraft } from "@/lib/templateForm";

export type TemplateFormState = {
  id: string;
  name: string;
  description: string;
  category: string;
  preview_cover: string;
  style_prefix: string;
  character_prompt: string;
  extra_prompt: string;
  negative_prompt: string;
  default_ratio: string;
  shot_duration_min: number;
  shot_duration_max: number;
  llm_system_addon: string;
  sort_order: number;
  is_active: boolean;
  is_premium: boolean;
};

type TemplateEditorDialogProps = {
  open: boolean;
  saving: boolean;
  editing: AdminTemplate | null;
  form: TemplateFormState;
  categorySuggestions?: string[];
  onOpenChange: (open: boolean) => void;
  onChange: (patch: Partial<TemplateFormState>) => void;
  onSave: () => void;
};

type EditorTab = "basic" | "prompts" | "publish";

const TABS: { id: EditorTab; label: string }[] = [
  { id: "basic", label: "基础信息" },
  { id: "prompts", label: "提示词" },
  { id: "publish", label: "发布设置" },
];

const RATIO_OPTIONS = [
  { value: "16:9", label: "16:9 横屏" },
  { value: "9:16", label: "9:16 竖屏" },
  { value: "1:1", label: "1:1 方形" },
  { value: "4:3", label: "4:3" },
  { value: "3:4", label: "3:4" },
];

// 解析封面预览地址
function coverPreviewSrc(url: string): string {
  const trimmed = (url || "").trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) return trimmed;
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

// 模板新建/编辑弹窗（分 Tab + 封面预览）
export function TemplateEditorDialog({
  open,
  saving,
  editing,
  form,
  categorySuggestions = [],
  onOpenChange,
  onChange,
  onSave,
}: TemplateEditorDialogProps) {
  const [tab, setTab] = useState<EditorTab>("basic");
  const [attempted, setAttempted] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const errors = validateTemplateDraft(form, !editing);
  const previewSrc = useMemo(() => coverPreviewSrc(form.preview_cover), [form.preview_cover]);
  useEffect(() => { if (open) { setTab("basic"); setAttempted(false); } }, [open]);

  function requestSave() {
    if (saving) return;
    setAttempted(true);
    if (Object.keys(errors).length) {
      setTab("basic");
      requestAnimationFrame(() => {
        const invalid = editorRef.current?.querySelector<HTMLElement>("[aria-invalid='true']");
        invalid?.focus({ preventScroll: true });
        invalid?.closest(".admin-form-field")?.scrollIntoView({ block: "center" });
      });
      return;
    }
    onSave();
  }

  const handleOpenChange = (next: boolean) => {
    if (saving) return;
    if (!next) setTab("basic");
    onOpenChange(next);
  };

  return (
    <AdminModal
      open={open}
      onOpenChange={handleOpenChange}
      size="full"
      className="template-editor-dialog max-h-[92vh] overflow-hidden"
      bodyClassName="p-0 overflow-hidden"
      title={editing ? `编辑模板 · ${editing.name}` : "新建模板"}
      subtitle={editing ? editing.id : "先填写展示信息与生成默认值，再按需配置提示词和上架状态"}
      footer={
        <>
          <Button variant="outline" disabled={saving} onClick={() => handleOpenChange(false)}>
            取消
          </Button>
          <Button disabled={saving} onClick={requestSave}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            {saving ? "保存中…" : "保存模板"}
          </Button>
        </>
      }
    >
      <div ref={editorRef} className="template-editor-layout" inert={saving}>
        <aside className="template-editor-preview">
          <div className="template-editor-preview-frame">
            {previewSrc ? (
              <img src={previewSrc} alt="封面预览" className="template-editor-preview-img" />
            ) : (
              <div className="template-editor-preview-empty">
                <ImageOff className="h-10 w-10 text-[#c0c4cc]" />
                <span>封面预览</span>
              </div>
            )}
          </div>
          <div className="settings-price-note"><strong>{form.name || "未命名模板"}</strong><br />{form.default_ratio} · {form.shot_duration_min}–{form.shot_duration_max} 秒 / 镜<br />{form.is_active ? "保存后上架展示" : "保存为下架状态"}</div>
          <AdminField label="封面 URL" hint="支持相对路径（/static）或 HTTPS；建议 16:9 横图。">
            <Input
              className="admin-input h-9"
              placeholder="/static/templates/covers/xxx.png"
              value={form.preview_cover}
              onChange={(e) => onChange({ preview_cover: e.target.value })}
            />
          </AdminField>
        </aside>

        <div className="template-editor-main">
          <div className="template-editor-tabs" role="tablist">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                className={cn("template-editor-tab", tab === item.id && "is-active")}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="template-editor-panel">
            {attempted && Object.keys(errors).length ? <div role="alert" className="template-editor-validation">请先完善基础信息：{Object.values(errors).join("；")}。</div> : null}
            {tab === "basic" ? (
              <div className="template-editor-grid">
                <h3 className="template-editor-section-title">展示信息</h3>
                {!editing ? (
                  <AdminField label="模板 ID" required error={attempted ? errors.id : undefined} hint="模板的唯一标识，创建后不可修改。" className="template-editor-field--full">
                    <Input
                      className="admin-input h-9"
                      placeholder="例如 live_street_interview"
                      maxLength={64}
                      value={form.id}
                      onChange={(e) => onChange({ id: e.target.value })}
                    />
                  </AdminField>
                ) : null}
                <AdminField label="模板名称" required error={attempted ? errors.name : undefined} hint="显示在用户选择模板的列表中。" className="template-editor-field--full">
                  <Input
                    className="admin-input h-9"
                    maxLength={128}
                    value={form.name}
                    onChange={(e) => onChange({ name: e.target.value })}
                  />
                </AdminField>
                <AdminField label="模板简介" optional hint="简要说明适合的题材与视觉效果。" className="template-editor-field--full">
                  <Textarea
                    value={form.description}
                    onChange={(e) => onChange({ description: e.target.value })}
                    className="min-h-[88px]"
                  />
                </AdminField>
                <AdminField
                  label="分类标签"
                  hint={
                    categorySuggestions.length
                      ? `常用：${categorySuggestions.slice(0, 8).join("、")}${categorySuggestions.length > 8 ? "…" : ""}`
                      : "多个标签用英文逗号分隔。"
                  }
                >
                  <Input
                    className="admin-input h-9"
                    placeholder="真人感,电影感,商业"
                    value={form.category}
                    onChange={(e) => onChange({ category: e.target.value })}
                  />
                  {categorySuggestions.length ? <div className="template-category-suggestions">{categorySuggestions.slice(0, 8).map((category) => <button type="button" key={category} onClick={() => {
                    const current = form.category.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
                    if (!current.includes(category)) onChange({ category: [...current, category].join(",") });
                  }}>+ {category}</button>)}</div> : null}
                </AdminField>
                <h3 className="template-editor-section-title">生成默认值</h3>
                <AdminSelect
                  label="默认画幅"
                  value={form.default_ratio}
                  options={RATIO_OPTIONS}
                  onChange={(v) => onChange({ default_ratio: v })}
                />
                <AdminField label="展示排序" error={attempted ? errors.sort_order : undefined} hint="数值越小越靠前，可使用负数。">
                  <Input
                    className="admin-input h-9"
                    type="number"
                    value={form.sort_order}
                    onChange={(e) => onChange({ sort_order: Number(e.target.value) })}
                  />
                </AdminField>
                <AdminField label="最短镜头时长（秒）" required error={attempted ? errors.shot_duration_min : undefined}>
                  <Input
                    className="admin-input h-9"
                    type="number"
                    min={1}
                    step={1}
                    value={form.shot_duration_min}
                    onChange={(e) => onChange({ shot_duration_min: Number(e.target.value) })}
                  />
                </AdminField>
                <AdminField label="最长镜头时长（秒）" required error={attempted ? errors.shot_duration_max : undefined} hint="不得小于最短时长。">
                  <Input
                    className="admin-input h-9"
                    type="number"
                    min={form.shot_duration_min || 1}
                    step={1}
                    value={form.shot_duration_max}
                    onChange={(e) => onChange({ shot_duration_max: Number(e.target.value) })}
                  />
                </AdminField>
              </div>
            ) : null}

            {tab === "prompts" ? (
              <div className="template-editor-grid template-editor-grid--prompts">
                <AdminField label="风格提示词" optional hint="描述画面风格、光线与色彩，会作为生成的基础风格。" className="template-editor-field--full">
                  <Textarea
                    value={form.style_prefix}
                    onChange={(e) => onChange({ style_prefix: e.target.value })}
                    className="min-h-[100px] font-mono text-[13px]"
                  />
                </AdminField>
                <AdminField label="角色提示词" optional hint="用于角色图像的外观与服饰要求。" className="template-editor-field--full">
                  <Textarea
                    value={form.character_prompt}
                    onChange={(e) => onChange({ character_prompt: e.target.value })}
                    className="min-h-[88px] font-mono text-[13px]"
                  />
                </AdminField>
                <AdminField label="额外提示词" optional hint="补充图片生成的统一要求。" className="template-editor-field--full">
                  <Textarea
                    value={form.extra_prompt}
                    onChange={(e) => onChange({ extra_prompt: e.target.value })}
                    className="min-h-[88px] font-mono text-[13px]"
                  />
                </AdminField>
                <AdminField label="文本模型附加说明" optional hint="追加给文本模型的脚本与规划说明。" className="template-editor-field--full">
                  <Textarea
                    value={form.llm_system_addon}
                    onChange={(e) => onChange({ llm_system_addon: e.target.value })}
                    className="min-h-[88px] font-mono text-[13px]"
                  />
                </AdminField>
                <AdminField label="负面提示词" optional hint="列出希望避免出现的画面内容或效果。" className="template-editor-field--full">
                  <Textarea
                    value={form.negative_prompt}
                    onChange={(e) => onChange({ negative_prompt: e.target.value })}
                    className="min-h-[88px] font-mono text-[13px]"
                  />
                </AdminField>
              </div>
            ) : null}

            {tab === "publish" ? (
              <div className="template-editor-publish">
                <div className="template-editor-publish-row">
                  <div>
                    <strong>上架展示</strong>
                    <p>关闭后用户端选模板列表不可见，已有项目不受影响。</p>
                  </div>
                  <Switch checked={form.is_active} onCheckedChange={(v) => onChange({ is_active: v })} />
                </div>
                <div className="template-editor-publish-row">
                  <div>
                    <strong>Premium 模板</strong>
                    <p>标记为高级模板，可用于权限或计费策略区分。</p>
                  </div>
                  <Switch checked={form.is_premium} onCheckedChange={(v) => onChange({ is_premium: v })} />
                </div>
                {editing ? (
                  <details className="mt-4 rounded-lg border p-3">
                    <summary className="cursor-pointer text-sm font-medium">高级配置（只读）</summary>
                    <div className="mt-3 space-y-3">
                      <div>
                        <div className="mb-1 text-xs text-[var(--admin-muted)]">seedance_config</div>
                        <pre className="admin-json-readonly">
                          {JSON.stringify(editing.seedance_config ?? {}, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <div className="mb-1 text-xs text-[var(--admin-muted)]">audio_config</div>
                        <pre className="admin-json-readonly">
                          {JSON.stringify(editing.audio_config ?? {}, null, 2)}
                        </pre>
                      </div>
                      <div>
                        <div className="mb-1 text-xs text-[var(--admin-muted)]">subtitle_config</div>
                        <pre className="admin-json-readonly">
                          {JSON.stringify(editing.subtitle_config ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>
                  </details>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </AdminModal>
  );
}
