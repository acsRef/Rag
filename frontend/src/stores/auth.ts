import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(loadUser())
  const token = ref<string | null>(localStorage.getItem('token'))

  function loadUser(): User | null {
    try {
      return JSON.parse(localStorage.getItem('user') || 'null')
    } catch {
      return null
    }
  }

  function save() {
    if (token.value) localStorage.setItem('token', token.value)
    if (user.value) localStorage.setItem('user', JSON.stringify(user.value))
    else { localStorage.removeItem('user') }
  }

  function clear() {
    token.value = null
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const permissions = computed(() => user.value?.permissions || [])
  const isAdmin = computed(() => permissions.value.includes('admin'))

  function hasPermission(p: string) {
    return permissions.value.includes(p) || permissions.value.includes('admin')
  }

  async function login(username: string, password: string) {
    const res = await authApi.login(username, password)
    token.value = res.data.access_token
    user.value = res.data.user
    save()
  }

  async function register(username: string, password: string, displayName?: string, email?: string) {
    const res = await authApi.register(username, password, displayName, email)
    token.value = res.data.access_token
    user.value = res.data.user
    save()
  }

  async function checkSession() {
    if (!token.value) return false
    try {
      const res = await authApi.me()
      user.value = res.data
      save()
      return true
    } catch {
      clear()
      return false
    }
  }

  function logout() {
    clear()
  }

  return {
    user, token, isLoggedIn, permissions, isAdmin,
    hasPermission, login, register, checkSession, logout,
  }
})
