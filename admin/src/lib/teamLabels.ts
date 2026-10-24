/** 团队日期统一按上海时区展示，避免服务器与浏览器时区差异。 */
export function teamDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

/** 平台管理员查看的团队内角色名称。 */
export const teamRoleLabels = { owner: "所有者", admin: "管理员", member: "成员" };
