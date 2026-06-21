<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>Register</h1>
      <p class="subtitle">Create your RAGent account</p>

      <div class="form-group">
        <label>Username</label>
        <input v-model="username" class="form-input" placeholder="Choose a username" @keyup.enter="doRegister" />
      </div>
      <div class="form-group">
        <label>Display Name (optional)</label>
        <input v-model="displayName" class="form-input" placeholder="How others see you" @keyup.enter="doRegister" />
      </div>
      <div class="form-group">
        <label>Email (optional)</label>
        <input v-model="email" class="form-input" type="email" placeholder="your@email.com" @keyup.enter="doRegister" />
      </div>
      <div class="form-group">
        <label>Password</label>
        <input v-model="password" class="form-input" type="password" placeholder="At least 6 characters" @keyup.enter="doRegister" />
      </div>

      <p v-if="error" style="color:var(--red);font-size:13px;margin-bottom:12px">{{ error }}</p>

      <button class="btn btn-primary btn-block" :disabled="loading" @click="doRegister">
        {{ loading ? 'Registering...' : 'Register' }}
      </button>

      <div class="form-footer">
        Already have an account? <router-link to="/login">Sign in</router-link>
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
const displayName = ref('')
const email = ref('')
const loading = ref(false)
const error = ref('')

async function doRegister() {
  if (!username.value || !password.value) return
  if (password.value.length < 6) { error.value = 'Password must be at least 6 characters'; return }
  loading.value = true
  error.value = ''
  try {
    await auth.register(username.value, password.value, displayName.value || undefined, email.value || undefined)
    router.push('/chat')
  } catch (err: any) {
    error.value = err.response?.data?.detail || 'Registration failed'
  } finally {
    loading.value = false
  }
}
</script>
