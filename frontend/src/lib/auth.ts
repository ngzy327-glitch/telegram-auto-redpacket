// Web 控制台认证：token 存取 + 当前用户信息
const TOKEN_KEY = "rp_token"
const USER_KEY = "rp_user"

export interface WebUser {
  id: number
  username: string
  is_admin: boolean
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function setUser(u: WebUser) {
  localStorage.setItem(USER_KEY, JSON.stringify(u))
}

export function getUser(): WebUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as WebUser
  } catch {
    return null
  }
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

/**
 * 给需要认证的图片资源 URL 追加 token 查询参数。
 * 浏览器 <img src> 无法携带 Authorization 头，故用 query token 兜底。
 */
export function authUrl(url: string | null | undefined): string {
  if (!url) return ""
  const token = getToken()
  if (!token) return url
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}token=${encodeURIComponent(token)}`
}
