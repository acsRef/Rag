<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>Sign in</h1>
      <p class="subtitle">Welcome back to RAGent</p>

      <div class="form-group">
        <label>Username</label>
        <input v-model="username" class="form-input" placeholder="Enter your username" @keyup.enter="doLogin" />
      </div>
      <div class="form-group">
        <label>Password</label>
        <input v-model="password" class="form-input" type="password" placeholder="Enter your password" @keyup.enter="doLogin" />
      </div>

      <p v-if="error" style="color:var(--red);font-size:13px;margin-bottom:12px">{{ error }}</p>

      <button class="btn btn-primary btn-block" :disabled="loading" @click="doLogin">
        {{ loading ? 'Signing in...' : 'Sign in' }}
      </button>

      <div class="form-footer">
        Don't have an account? <router-link to="/register">Register</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function doLogin() {
  if (!username.value || !password.value) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/chat')
  } catch (err: any) {
    error.value = err.response?.data?.detail || 'Login failed'
  } finally {
    loading.value = false
  }
}
</script>
