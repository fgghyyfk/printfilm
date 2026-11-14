type TemplateDraft = {
  id: string; name: string; shot_duration_min: number; shot_duration_max: number; sort_order: number;
};

export function validateTemplateDraft(draft: TemplateDraft, creating: boolean) {
  const errors: Partial<Record<keyof TemplateDraft, string>> = {};
  if (creating && (!draft.id.trim() || draft.id.trim().length > 64)) errors.id = "请填写 1–64 个字符的模板 ID";
  if (!draft.name.trim() || draft.name.trim().length > 128) errors.name = "请填写 1–128 个字符的模板名称";
  if (!Number.isSafeInteger(draft.shot_duration_min) || draft.shot_duration_min <= 0) errors.shot_duration_min = "最短时长须为大于 0 的整数秒";
  if (!Number.isSafeInteger(draft.shot_duration_max) || draft.shot_duration_max <= 0) errors.shot_duration_max = "最长时长须为大于 0 的整数秒";
  if (!errors.shot_duration_min && !errors.shot_duration_max && draft.shot_duration_max < draft.shot_duration_min) errors.shot_duration_max = "最长时长不能小于最短时长";
  if (!Number.isSafeInteger(draft.sort_order)) errors.sort_order = "排序值须为整数";
  return errors;
}
