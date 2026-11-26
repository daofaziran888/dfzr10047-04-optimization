import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import DeviationRepairView from '@/views/DeviationRepairView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 偏差修复确认页有分来源分组与复验单动作，必须排在动态模块路由之前。
    { path: '/deviation_repair', name: 'deviation_repair', component: DeviationRepairView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
