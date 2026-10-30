import { clearAuth, getToken, setCachedUser, setToken, type AdminUser } from "@/lib/auth";

type ApiError = {
  detail?: string | { msg?: string }[];
};

// 将 FastAPI 的校验错误或业务错误转换为可读文案
function errorMessage(data: ApiError, status: number): string {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return `请求失败 (${status})`;
}

// 为管理接口附加会话信息，统一处理响应与登录失效
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(path, { ...options, headers });
  const text = await res.text();
  const data = text ? (JSON.parse(text) as ApiError & T) : ({} as T);
  // 登录 401 = 账号密码错误；其它 401 = 会话失效
  const isLoginRequest = path.includes("/api/auth/login");
  if (res.status === 401 && !isLoginRequest) {
    clearAuth();
    // 登录页地址要带部署前缀（如 /admin/login）
    const loginPath = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/login`;
    if (!window.location.pathname.startsWith(loginPath)) {
      window.location.href = loginPath;
    }
    throw new Error("未登录或登录已失效");
  }
  if (!res.ok) {
    throw new Error(errorMessage(data as ApiError, res.status));
  }
  return data as T;
}

// 登录后读取用户资料并验证管理员权限
export async function loginAsAdmin(email: string, password: string): Promise<AdminUser> {
  const tokenRes = await api<{ access_token: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(tokenRes.access_token);
  const me = await api<AdminUser>("/api/auth/me");
  if (me.role !== "admin") {
    clearAuth();
    throw new Error("该账号没有管理员权限");
  }
  setCachedUser(me);
  return me;
}

// 保持既有导入路径兼容，新增接口类型按领域存放在 types/。
export type * from "./types/common";
export type * from "./types/analytics";
export type * from "./types/finance";
export type * from "./types/projects";
export type * from "./types/tasks";
export type * from "./types/settings";
