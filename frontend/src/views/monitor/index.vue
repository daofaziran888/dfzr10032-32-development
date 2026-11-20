<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div>
        <h2>采集监控管理</h2>
        <p class="page-desc">维护监控面板，并由采集故障明细登记告警；告警保留对应日志组与本次严重程度。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记监控面板</button>
        <button class="btn" type="button" @click="generateAlerts">按故障生成告警</button>
        <button class="btn" type="button" @click="exportRows">导出采集监控清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statValues" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集监控数据，可先登记监控面板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集监控记录</span>
      <span v-if="message" class="ok-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="sub-section">
      <header class="sub-head">
        <div>
          <h3>采集告警记录</h3>
          <p class="page-desc">一条采集故障对应一张告警；关闭只影响该告警自己的故障，同任务其他未处置故障仍会提示继续接单。</p>
        </div>
        <span class="sub-tip">待领取 {{ pendingAlerts }} 条 · 未关闭 {{ openAlerts }} 条</span>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in alertColumns" :key="column">{{ column }}</th>
            <th>日志组</th>
            <th>同任务未处置</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alert in alerts" :key="String(alert.id)">
            <td v-for="column in alertColumns" :key="column" :title="String(alert[column] ?? '')">
              {{ formatCell(alert[column]) }}
            </td>
            <td>{{ (alert['日志组'] as string[] | undefined)?.join('、') || '—' }}</td>
            <td>{{ formatOpenFaults(alert) }}</td>
            <td class="row-actions">
              <button
                v-for="action in alertActions"
                :key="action"
                class="link"
                type="button"
                @click="runAlertAction(action, alert)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!alerts.length">
            <td :colspan="alertColumns.length + 3" class="empty-state">暂无采集告警，可先按故障生成告警</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ alertTotal }} 条告警记录</span>
        <span v-if="alertMessage" class="ok-text">{{ alertMessage }}</span>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记监控面板</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, unknown>
type AlertRow = Record<string, unknown> & { 同任务未处置?: { 故障编号?: string; 当前级别?: string; 重复次数?: number }[] }

const ENDPOINT = '/api/monitor'
const ALERT_ENDPOINT = '/api/monitor/alerts'
const columns = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"]
const actions = ["查看详情"]
const alertColumns = ["告警编号", "关联任务", "故障编号", "本次严重程度", "重复次数", "告警状态", "领取人"]
const alertActions = ["领取告警", "关闭告警", "忽略告警"]
const statValues = ref([{"label": "待领取告警", "value": 0}, {"label": "未关闭告警", "value": 0}, {"label": "告警总数", "value": 0}])

const rows = ref<Row[]>([])
const total = ref(0)
const alerts = ref<AlertRow[]>([])
const alertTotal = ref(0)
const pendingAlerts = ref(0)
const openAlerts = ref(0)
const errorMessage = ref('')
const message = ref('')
const alertMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  const text = String(value)
  return text.length > 40 ? `${text.slice(0, 40)}…` : text
}

function formatOpenFaults(alert: AlertRow) {
  const items = alert['同任务未处置'] ?? []
  if (!items.length) return '无'
  return items
    .map((item) => `${item.故障编号 ?? '—'}(${item.当前级别 ?? '—'}，${item.重复次数 ?? 0} 次)`)
    .join('、')
}

function updateStats() {
  pendingAlerts.value = alerts.value.filter((item) => item['告警状态'] === '待领取').length
  openAlerts.value = alerts.value.filter((item) => item['告警状态'] !== '已关闭' && item['告警状态'] !== '已忽略').length
  statValues.value[0].value = pendingAlerts.value
  statValues.value[1].value = openAlerts.value
  statValues.value[2].value = alertTotal.value
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  message.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '登记失败')
    }
    message.value = data.message ?? '监控面板已登记'
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '采集监控动作未生效，请稍后重试')
    }
    message.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集监控操作失败'
  }
}

async function runAlertAction(action: string, alert: AlertRow) {
  alertMessage.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ALERT_ENDPOINT}/${alert.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '告警动作未生效，请稍后重试')
    }
    alertMessage.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集告警操作失败'
  }
}

async function generateAlerts() {
  alertMessage.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ALERT_ENDPOINT}/generate`, { method: 'POST' })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '告警生成失败')
    }
    alertMessage.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [panelResponse, alertResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ALERT_ENDPOINT}?size=200`),
    ])
    if (!panelResponse.ok) {
      throw new Error('监控面板列表读取失败')
    }
    if (!alertResponse.ok) {
      throw new Error('采集告警列表读取失败')
    }
    const payload = await panelResponse.json()
    const alertPayload = await alertResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    alerts.value = alertPayload.items ?? []
    alertTotal.value = alertPayload.total ?? alerts.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集监控列表读取失败'
  }
}

onMounted(reload)
</script>
