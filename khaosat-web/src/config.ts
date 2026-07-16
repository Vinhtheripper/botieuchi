export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

export function apiUrl(path: string) {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

export function mediaUrl(path?: string) {
  if (!path || /^https?:\/\//.test(path)) return path || ''
  if (!API_BASE_URL.startsWith('http')) return path
  return `${new URL(API_BASE_URL).origin}${path.startsWith('/') ? path : `/${path}`}`
}
