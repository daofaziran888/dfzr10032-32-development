<template>
  <section class="page" data-module="log_viewer">
    <header class="page-head">
      <div>
        <h2>采集日志管理</h2>
        <p class="page-desc">维护日志条目，并按「关联任务 + 错误堆栈」归并出采集故障，供采集监控登记告警。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记日志条目</button>
        <button class="btn" type="button" @click="groupFaults">执行故障归并</button>
        <button class="btn" type="button" @click="exportRows">导出采集日志清单</button>
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
          <th>故障编号</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column" :title="String(row[column] ?? '')">
            {{ formatCell(row, column) }}
          </td>
          <td>{{ row['故障编号'] ?? '未归并' }}</td>
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
          <td :colspan="columns.length + 2" class="empty-state">暂无采集日志数据，可先登记日志条目</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集日志记录</span>
      <span v-if="message" class="ok-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section class="sub-section">
      <header class="sub-head">
        <div>
          <h3>采集故障明细</h3>
          <p class="page-desc">同一任务下按错误堆栈各成一条故障；已处置的故障再次出现会新开记录，重复次数只统计未重复计入的原始日志。</p>
        </div>
        <span class="sub-tip">共 {{ faults.length }} 条故障 · 未归并日志 {{ ungrouped }} 条</span>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in faultColumns" :key="column">{{ column }}</th>
            <th>日志组</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="fault in faults" :key="String(fault.id)">
            <td v-for="column in faultColumns" :key="column" :title="String(fault[column] ?? '')">
              {{ formatCell(fault, column) }}
            </td>
            <td>{{ (fault['日志组'] as string[] | undefined)?.join('、') || '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in faultActions"
                :key="action"
                class="link"
                type="button"
                @click="runFaultAction(action, fault)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!faults.length">
            <td :colspan="faultColumns.length + 2" class="empty-state">暂无采集故障，可先执行故障归并</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>故障按首次出现时间保留，重复次数按原始日志逐条累计</span>
        <span v-if="faultMessage" class="ok-text">{{ faultMessage }}</span>
      </footer>
    </section>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记日志条目</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in createFieldsList" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <textarea v-if="field === '错误堆栈' || field === '日志内容'" v-model="createFields[field]" rows="3" />
              <input v-else v-model="createFields[field]" :placeholder="`请输入${field}`" :required="requiredFields.includes(field)" />
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

const ENDPOINT = '/api/log_viewer'
const FAULT_ENDPOINT = '/api/log_viewer/faults'
const columns = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈", "处理状态", "处理人"]
const actions = ["开始处理", "标记已处理", "忽略日志"]
const faultColumns = ["故障编号", "关联任务", "故障摘要", "首次出现时间", "重复次数", "最高级别", "处理状态"]
const faultActions = ["开始处理", "标记已处理", "关闭故障", "忽略故障"]
const requiredFields = ["日志编号", "关联任务", "日志级别"]
const createFieldsList = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈"]
const statValues = ref([{"label": "未处理日志", "value": 0}, {"label": "处理中日志", "value": 0}, {"label": "已处理日志", "value": 0}])

const rows = ref<Row[]>([])
const total = ref(0)
const faults = ref<Row[]>([])
const ungrouped = ref(0)
const errorMessage = ref('')
const message = ref('')
const faultMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})

function updateStats() {
  const data = rows.value
  statValues.value.length > 0 && (statValues.value[0].value = data.filter((r: Row) => r.status === '未处理' || r.pending).length)
  statValues.value.length > 1 && (statValues.value[1].value = data.filter((r: Row) => r.status === '处理中').length)
  statValues.value.length > 2 && (statValues.value[2].value = data.filter((r: Row) => r.status === '已处理').length)
}

function formatCell(row: Row, column: string) {
  const value = row[column]
  if (value === null || value === undefined || value === '') return '—'
  const text = String(value)
  return text.length > 48 ? `${text.slice(0, 48)}…` : text
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
    message.value = data.message ?? '日志条目已登记'
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
      throw new Error(data.message || '采集日志动作未生效，请稍后重试')
    }
    message.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志操作失败'
  }
}

async function runFaultAction(action: string, fault: Row) {
  faultMessage.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${FAULT_ENDPOINT}/${fault.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '采集故障动作未生效，请稍后重试')
    }
    faultMessage.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集故障操作失败'
  }
}

async function groupFaults() {
  errorMessage.value = ''
  faultMessage.value = ''
  try {
    const response = await request(`${FAULT_ENDPOINT}/group`, { method: 'POST' })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      throw new Error(data.message || '故障归并失败')
    }
    faultMessage.value = data.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '故障归并失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [logResponse, faultResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${FAULT_ENDPOINT}?size=200`),
    ])
    if (!logResponse.ok) {
      throw new Error('日志条目列表读取失败')
    }
    if (!faultResponse.ok) {
      throw new Error('采集故障列表读取失败')
    }
    const payload = await logResponse.json()
    const faultPayload = await faultResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    faults.value = faultPayload.items ?? []
    ungrouped.value = rows.value.filter((row) => !row['故障编号']).length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志列表读取失败'
  }
}

onMounted(reload)
</script>
