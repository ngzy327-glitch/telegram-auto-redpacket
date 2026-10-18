<script setup lang="ts">
import { ref } from "vue"
import { Refresh, Delete, Monitor, Timer } from "@element-plus/icons-vue"

import { api } from "@/api"
import type { WebappLogItem } from "@/types"
import { WEBAPP_LINE_REASON, WEBAPP_STEP_LABEL } from "@/types"
import { usePolling } from "@/composables/usePolling"

const items = ref<WebappLogItem[]>([])
const loading = ref(true)
const page = ref(1)
const total = ref(0)
const pageSize = ref(20)
// "" 全部 | ok 成功 | fail 失败
const resultFilter = ref("")
const autoRefresh = ref(true)

async function load() {
  loading.value = true
  try {
    const r = await api.getWebappLogs(page.value, pageSize.value, resultFilter.value)
    items.value = r.items
    total.value = r.total
  } catch (e) {
    ElMessage.error("加载日志失败：" + (e as Error).message)
  } finally {
    loading.value = false
  }
}

// 5 秒轻轮询，方便实时盯领取过程（切后台/切页自动暂停）
const polling = usePolling(() => (autoRefresh.value ? load() : Promise.resolve()), 5000)

function onFilterChange() {
  page.value = 1
  load()
}

function onAutoRefreshChange(v: boolean) {
  if (v) polling.start()
  else polling.stop()
}

function onPageChange(p: number) {
  page.value = p
  load()
}

async function onClear() {
  try {
    await ElMessageBox.confirm("确定清空当前可见的全部 WebApp 日志？此操作不可恢复。", "清空确认", {
      type: "warning",
      confirmButtonText: "清空",
      cancelButtonText: "取消",
    })
  } catch {
    return
  }
  try {
    const r = await api.clearWebappLogs()
    ElMessage.success(`已清空 ${r.deleted} 条日志`)
    page.value = 1
    load()
  } catch (e) {
    ElMessage.error("清空失败：" + (e as Error).message)
  }
}

function formatTime(iso: string) {
  if (!iso) return ""
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

// 毫秒 → 人性化耗时
function fmtMs(v: number | null) {
  if (v === null || v === undefined) return ""
  if (v < 1000) return `${v}ms`
  return `${(v / 1000).toFixed(2)}s`
}

function outputPreview(row: WebappLogItem) {
  if (row.amount) return row.amount
  if (row.output) return row.output.split("\n")[0]
  return ""
}
</script>

<template>
  <div class="webapp-logs-page">
    <el-card shadow="never">
      <template #header>
        <div class="table-header">
          <span class="card-title">
            <el-icon class="title-icon"><Monitor /></el-icon>
            WebApp 日志
          </span>
          <div class="table-actions">
            <el-radio-group
              :model-value="resultFilter"
              size="small"
              @update:model-value="(v: string) => (resultFilter = v, onFilterChange())"
            >
              <el-radio-button label="">全部</el-radio-button>
              <el-radio-button label="ok">成功</el-radio-button>
              <el-radio-button label="fail">失败</el-radio-button>
            </el-radio-group>
            <el-tooltip content="开启后每 5 秒自动刷新" placement="top">
              <el-switch
                :model-value="autoRefresh"
                active-text="自动刷新"
                inline-prompt
                @update:model-value="onAutoRefreshChange"
              />
            </el-tooltip>
            <el-button :icon="Refresh" @click="load">刷新</el-button>
            <el-button :icon="Delete" type="danger" plain @click="onClear">清空</el-button>
          </div>
        </div>
      </template>

      <el-skeleton v-if="loading" :rows="5" animated />

      <el-empty v-else-if="items.length === 0" description="暂无 WebApp 领取日志" />

      <template v-else>
        <el-table :data="items" style="width: 100%" row-key="id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="detail">
                <div v-if="row.lines && row.lines.length" class="detail-block">
                  <div class="detail-title">模型线路（并发）</div>
                  <div class="line-list">
                    <div v-for="ln in row.lines" :key="ln.tag" class="line-row">
                      <el-tag
                        :type="ln.ok ? 'success' : 'info'"
                        size="small"
                        effect="plain"
                        class="line-tag"
                      >
                        {{ ln.ok ? "已提交" : "失败" }}
                      </el-tag>
                      <span class="line-name">{{ ln.tag }}</span>
                      <span class="line-ms">
                        <el-icon><Timer /></el-icon>
                        {{ ln.ms !== null ? fmtMs(ln.ms) : "—" }}
                      </span>
                      <span v-if="!ln.ok && ln.reason" class="line-reason">
                        {{ WEBAPP_LINE_REASON[ln.reason] || ln.reason }}
                      </span>
                      <el-tag
                        v-if="ln.ok && row.winner === ln.tag"
                        type="warning"
                        size="small"
                        effect="dark"
                      >
                        胜出
                      </el-tag>
                    </div>
                  </div>
                </div>
                <div v-if="row.output" class="detail-block">
                  <div class="detail-title">领取输出</div>
                  <pre class="output-pre">{{ row.output }}</pre>
                </div>
                <el-empty v-else description="无输出" :image-size="50" />
              </div>
            </template>
          </el-table-column>

          <el-table-column label="时间" width="130">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>

          <el-table-column label="账号" prop="account_name" width="100" />
          <el-table-column label="群组" prop="chat" min-width="120" show-overflow-tooltip />
          <el-table-column label="Bot" prop="bot" width="120" show-overflow-tooltip />

          <el-table-column label="①点击网页" width="95" align="center">
            <template #default="{ row }">
              <span :class="{ muted: row.ms_webview === null, reached: row.step !== 'start' }">
                {{ fmtMs(row.ms_webview) || "—" }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="②执行操作" width="95" align="center">
            <template #default="{ row }">
              <span :class="{ muted: row.ms_submit === null, reached: row.step === 'submit' || row.step === 'done' }">
                {{ fmtMs(row.ms_submit) || "—" }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="③等回执" width="95" align="center">
            <template #default="{ row }">
              <span :class="{ muted: row.ms_result === null, reached: row.step === 'done' }">
                {{ fmtMs(row.ms_result) || "—" }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="结果 / 失败原因" min-width="180">
            <template #default="{ row }">
              <div class="result-cell">
                <el-tag :type="row.ok ? 'success' : 'danger'" effect="light" size="small">
                  {{ row.ok ? "领取成功" : "领取失败" }}
                </el-tag>
                <el-tag effect="plain" size="small" type="info" class="step-tag">
                  {{ WEBAPP_STEP_LABEL[row.step] || row.step }}
                </el-tag>
              </div>
              <div v-if="!row.ok && row.fail_reason" class="fail-reason">{{ row.fail_reason }}</div>
              <div v-if="row.ok && !row.amount" class="fail-reason muted">已提交，等待 bot 确认</div>
            </template>
          </el-table-column>

          <el-table-column label="金额 / 输出" min-width="140">
            <template #default="{ row }">
              <span :class="{ amount: !!row.amount, muted: !row.amount }">
                {{ outputPreview(row) || "—" }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="总耗时" width="90" align="right">
            <template #default="{ row }">
              <b>{{ fmtMs(row.total_ms) || "—" }}</b>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next"
            @current-change="onPageChange"
          />
        </div>
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.card-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
}

.title-icon {
  color: var(--el-color-primary);
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.table-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.reached {
  font-weight: 700;
  color: var(--el-color-success);
}

.result-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.step-tag {
  font-weight: 600;
}

.fail-reason {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-color-danger);
  line-height: 1.4;
}

.amount {
  font-weight: 700;
  color: var(--el-color-success);
}

.detail {
  padding: 8px 16px 12px;
}

.detail-block {
  margin-bottom: 10px;
}

.detail-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.line-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.line-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.line-name {
  font-weight: 600;
  min-width: 120px;
}

.line-ms {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.line-reason {
  color: var(--el-color-danger);
}

.output-pre {
  margin: 0;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  font-family: var(--el-font-family);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--el-text-color-regular);
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
