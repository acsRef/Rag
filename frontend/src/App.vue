<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()

onMounted(async () => {
  if (auth.token) {
    const valid = await auth.checkSession()
    if (!valid) {
      router.push('/login')
    }
  } else {
    router.push('/login')
  }
})
</script>
