<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "文件投递", "created": 0, "pending": 0, "abnormal": 0}, {"name": "格式探测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "编码归一", "created": 0, "pending": 0, "abnormal": 0}, {"name": "解析路由", "created": 0, "pending": 0, "abnormal": 0}, {"name": "抽取模板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "字段解析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "坏行隔离", "created": 0, "pending": 0, "abnormal": 0}, {"name": "解析批次", "created": 0, "pending": 0, "abnormal": 0}, {"name": "结构嗅探", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分隔规则", "created": 0, "pending": 0, "abnormal": 0}, {"name": "嵌套展平", "created": 0, "pending": 0, "abnormal": 0}, {"name": "取值转型", "created": 0, "pending": 0, "abnormal": 0}, {"name": "单位归一", "created": 0, "pending": 0, "abnormal": 0}, {"name": "输出契约", "created": 0, "pending": 0, "abnormal": 0}, {"name": "结果投递", "created": 0, "pending": 0, "abnormal": 0}, {"name": "解析水位", "created": 0, "pending": 0, "abnormal": 0}, {"name": "重解析任务", "created": 0, "pending": 0, "abnormal": 0}, {"name": "重复指纹", "created": 0, "pending": 0, "abnormal": 0}, {"name": "抽样复核", "created": 0, "pending": 0, "abnormal": 0}, {"name": "偏差修复", "created": 0, "pending": 0, "abnormal": 0}, {"name": "来源复验单", "created": 0, "pending": 0, "abnormal": 0}, {"name": "解析指标", "created": 0, "pending": 0, "abnormal": 0}, {"name": "模板差异", "created": 0, "pending": 0, "abnormal": 0}, {"name": "投递审计", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
