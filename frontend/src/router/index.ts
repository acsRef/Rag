import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const LoginView = () => import('../views/LoginView.vue')
const RegisterView = () => import('../views/RegisterView.vue')
const ChatView = () => import('../views/ChatView.vue')
const DocumentsView = () => import('../views/DocumentsView.vue')
const KBView = () => import('../views/KBView.vue')
const AppLayout = () => import('../components/AppLayout.vue')

const routes = [
  { path: '/login', component: LoginView, meta: { guest: true } },
  { path: '/register', component: RegisterView, meta: { guest: true } },
  {
    path: '/',
    component: AppLayout,
    meta: { auth: true },
    children: [
      { path: '', redirect: '/chat' },
      { path: 'chat', component: ChatView },
      { path: 'documents', component: DocumentsView },
      { path: 'kb', component: KBView },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()
  // Fire-and-forget:不等 checkSession 完成就放行,避免 /auth/me 临时失败阻塞跳转
  if (auth.isLoggedIn) {
    auth.checkSession()
  }
  if (to.meta.auth && !auth.isLoggedIn) {
    next('/login')
  } else if (to.meta.guest && auth.isLoggedIn) {
    next('/chat')
  } else {
    next()
  }
})

export default router
