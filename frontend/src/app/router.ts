import { createRouter, createWebHistory } from 'vue-router'

/**
 * Application router.
 *
 * PR-5a routes:
 *   /          → UploadPanel (upload + run lifecycle)
 *   /runs/:id  → RunProgress + ReviewPage placeholder (filled in PR-5b)
 *
 * PR-5b will replace the review placeholder with the full ReviewGrid composition.
 */
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'upload',
      component: () => import('@/features/run/UploadPanel.vue'),
      meta: { title: 'Subir PDF' },
    },
    {
      path: '/runs/:id',
      name: 'review',
      component: () => import('@/features/review/ReviewPage.vue'),
      meta: { title: 'Revisión' },
      props: true,
    },
    {
      // SDD#3 (RH-010): run-history list — past runs, retry, delete.
      path: '/historial',
      name: 'historial',
      component: () => import('@/features/run/RunHistoryPage.vue'),
      meta: { title: 'Historial' },
    },
  ],
})

// Sync document title with route meta
router.afterEach((to) => {
  const baseTitle = 'CTR Rosales QC'
  const routeTitle = typeof to.meta.title === 'string' ? to.meta.title : null
  document.title = routeTitle ? `${routeTitle} — ${baseTitle}` : baseTitle
})

export default router
