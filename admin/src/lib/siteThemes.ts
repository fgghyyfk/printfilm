/** 后台可选的全站主题说明；ID 须与后端 SITE_THEMES、用户端 lib/siteTheme.ts 一致。 */
export type SiteThemeMeta = {
  id: string;
  name: string;
  description: string;
  /* swatches 色板：背景、强调、文字 */
  swatches: [string, string, string];
  serif: boolean;
};

export const SITE_THEME_META: SiteThemeMeta[] = [
  {
    id: "daylight",
    name: "晨光",
    description: "暖白底、深松石绿、衬线标题，柔和留白，适合长期使用。",
    swatches: ["#f8f7f3", "#0f6e5f", "#1d2624"],
    serif: true,
  },
  {
    id: "studio",
    name: "演播室字幕",
    description: "墨黑顶栏、字幕黄选中态、朱红按钮，硬朗醒目。",
    swatches: ["#f3eee4", "#dc3a14", "#17130e"],
    serif: false,
  },
];

/** 用户端地址：生产与后台同源，开发环境是独立端口。 */
export function siteOrigin(devOrigin = "http://localhost:5173"): string {
  return import.meta.env.DEV ? devOrigin : window.location.origin;
}
