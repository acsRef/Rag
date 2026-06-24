import api from './index'

export interface User {
  id: string
  username: string
  display_name: string
  email: string
  is_active: boolean
  role_ids: number[]
  roles: string[]
  permissions: string[]
  workspace_kb_id: string
}

export interface LoginRes {
  access_token: string
  token_type: string
  user: User
}

export const authApi = {
  login(username: string, password: string) {
    return api.post<LoginRes>('/auth/login', { username, password })
  },
  register(username: string, password: string, display_name?: string, email?: string) {
    return api.post<LoginRes>('/auth/register', { username, password, display_name, email })
  },
  me() {
    return api.get<User>('/auth/me')
  },
}
