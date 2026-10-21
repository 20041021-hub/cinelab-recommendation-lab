import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/HomeView.vue'), meta: { title: '发现' } },
    { path: '/movies', component: () => import('./views/MoviesView.vue'), meta: { title: '电影库' } },
    { path: '/movies/:id', component: () => import('./views/DetailView.vue'), meta: { title: '电影详情' } },
    { path: '/login', component: () => import('./views/AuthView.vue'), meta: { title: '登录' } },
    { path: '/register', component: () => import('./views/AuthView.vue'), meta: { title: '注册' } },
    { path: '/profile', component: () => import('./views/ProfileView.vue'), meta: { title: '我的片单', requiresAuth: true } },
    { path: '/experiments', component: () => import('./views/ExperimentsView.vue'), meta: { title: '推荐实验室' } },
    { path: '/cold-start', component: () => import('./views/ColdStartView.vue'), meta: { title: '新用户冷启动' } },
    { path: '/:pathMatch(.*)*', component: () => import('./views/NotFoundView.vue'), meta: { title: '页面未找到' } },
  ],
  scrollBehavior(to, from, saved) { return saved || (to.path === from.path ? undefined : { top: 0 }) },
})
export function safeRedirect(value: unknown): string {
  return typeof value === 'string' && /^\/(?:movies(?:\/\d+)?|profile|experiments|cold-start)(?:\?|$)/.test(value) ? value : '/profile'
}
router.beforeEach(async to => {
  const auth = useAuth()
  await auth.restore()
  if (to.meta.requiresAuth && !auth.account && !auth.restoreError) return { path: '/login', query: { redirect: to.fullPath } }
})
router.afterEach(to => { document.title = `${to.meta.title} · 片刻 CineLab` })
export default router
