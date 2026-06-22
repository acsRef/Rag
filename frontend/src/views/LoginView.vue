<template>
  <div class="auth-page">
    <div class="auth-left">
      <div>
        <h1>RAGent</h1>
        <p class="tagline">智能知识库问答系统</p>
      </div>
      <ul class="feature-list">
        <li>
          <span class="icon">📄</span>
          文档解析 + 智能分块
        </li>
        <li>
          <span class="icon">🔍</span>
          向量检索 + 语义理解
        </li>
        <li>
          <span class="icon">💬</span>
          多轮对话 + 知识溯源
        </li>
      </ul>
      <p class="copyright">&copy; 2026 RAGent</p>
    </div>
    <div class="auth-right">
      <h2>登录</h2>
      <p class="subtitle">欢迎使用 RAGent</p>

      <div class="form-group">
        <label>用户名</label>
        <input
          v-model="username"
          class="form-input"
          placeholder="请输入用户名"
          @keyup.enter="doLogin"
        />
      </div>
      <div class="form-group">
        <label>密码</label>
        <input
          v-model="password"
          class="form-input"
          type="password"
          placeholder="请输入密码"
          @keyup.enter="doLogin"
        />
      </div>

      <p v-if="error" style="color:var(--red);font-size:12px;margin-bottom:10px">{{ error }}</p>

      <button class="btn btn-primary btn-block" :disabled="loading" @click="doLogin">
        {{ loading ? '登录中...' : '登录' }}
      </button>

      <div class="form-footer">
        还没有账号？<router-link to="/register">注册</router-link>
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
    error.value = err.response?.data?.detail || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
