import { api } from "./client";
import type { AdWordsCheckResult, AdWordsOverview } from "./types/settings";

/** 读取内置广告法词库概览与当前自定义词 */
export function fetchAdWordsOverview(): Promise<AdWordsOverview> {
  return api<AdWordsOverview>("/api/admin/settings/compliance/ad-words");
}

/** 用当前词库试检一段文本，返回清洗结果与命中词 */
export function checkAdWords(text: string): Promise<AdWordsCheckResult> {
  return api<AdWordsCheckResult>("/api/admin/settings/compliance/ad-words/check", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}
