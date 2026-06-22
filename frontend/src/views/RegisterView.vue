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
      <h2>注册</h2>
      <p class="subtitle">创建你的 RAGent 账号</p>

      <div class="form-group">
        <label>用户名</label>
        <input
          v-model="username"
          class="form-input"
          :class="{ 'form-input-error': usernameError }"
          placeholder="请选择用户名"
          @keyup.enter="doRegister"
        />
        <p v-if="usernameError" class="form-error">{{ usernameError }}</p>
      </div>
      <div class="form-group">
        <label>显示名称（可选）</label>
        <input v-model="displayName" class="form-input" placeholder="其他人如何称呼你" @keyup.enter="doRegister" />
      </div>
      <div class="form-group">
        <label>邮箱（可选）</label>
        <input v-model="email" class="form-input" type="email" placeholder="your@email.com" @keyup.enter="doRegister" />
      </div>
      <div class="form-group">
        <label>密码</label>
        <input
          v-model="password"
          class="form-input"
          :class="{ 'form-input-error': passwordError }"
          type="password"
          placeholder="至少 6 个字符"
          @keyup.enter="doRegister"
        />
        <p v-if="passwordError" class="form-error">{{ passwordError }}</p>
      </div>

      <p v-if="error" style="color:var(--red);font-size:12px;margin-bottom:10px">{{ error }}</p>

      <button class="btn btn-primary btn-block" :disabled="loading" @click="doRegister">
        {{ loading ? '注册中...' : '注册' }}
      </button>

      <div class="form-footer">
        已有账号？<router-link to="/login">登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
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
const touched = ref(false)

const usernameError = computed(() => {
  if (!touched.value || username.value.length >= 2) return ''
  return '用户名至少 2 个字符'
})

const passwordError = computed(() => {
  if (!touched.value) return ''
  if (password.value.length >= 6) return ''
  return password.value.length > 0 ? '密码至少 6 个字符' : ''
})

async function doRegister() {
  touched.value = true
  if (!username.value || !password.value || password.value.length < 6) return
  loading.value = true
  error.value = ''
  try {
    await auth.register(username.value, password.value, displayName.value || undefined, email.value || undefined)
    router.push('/chat')
  } catch (err: any) {
    error.value = err.response?.data?.detail || '注册失败'
  } finally {
    loading.value = false
  }
}
</script>
