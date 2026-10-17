<script setup lang="ts">
import { onMounted, reactive, ref } from "vue"
import { Plus, Delete } from "@element-plus/icons-vue"

import { api } from "@/api"
import { getUser } from "@/lib/auth"

interface UserRow {
  id: number
  username: string
  is_admin: boolean
  created_at: string
}

const rows = ref<UserRow[]>([])
const loading = ref(false)

const me = getUser()
const isAdmin = me?.is_admin ?? false

async function load() {
  if (!isAdmin) return
  loading.value = true
  try {
    rows.value = await api.listUsers()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

// 创建用户弹窗
const createVisible = ref(false)
const createForm = reactive({ username: "", password: "", is_admin: false })
const createLoading = ref(false)

async function onCreate() {
  if (!createForm.username || !createForm.password) {
    ElMessage.warning("请填写用户名和密码")
    return
  }
  createLoading.value = true
  try {
    await api.createUser(createForm.username, createForm.password, createForm.is_admin)
    ElMessage.success("用户已创建")
    createVisible.value = false
    createForm.username = ""
    createForm.password = ""
    createForm.is_admin = false
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    createLoading.value = false
  }
}

async function onDelete(row: UserRow) {
  if (row.id === me?.id) {
    ElMessage.warning("不能删除自己")
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username}」？该用户的 Telegram 账号将变为无主（可由管理员接管）。`,
      "删除用户",
      { type: "warning" }
    )
  } catch {
    return
  }
  try {
    await api.deleteUser(row.id)
    ElMessage.success("已删除")
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2>用户管理</h2>
      <p class="page-desc">管理控制台登录用户。每个用户只能看到自己的 Telegram 账号，管理员可见全部。</p>
    </div>

    <el-card v-if="!isAdmin" class="no-perm">
      <el-empty description="仅管理员可访问此页面" />
    </el-card>

    <template v-else>
      <div class="toolbar">
        <el-button type="primary" :icon="Plus" @click="createVisible = true">
          新建用户
        </el-button>
      </div>

      <el-table :data="rows" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="username" label="用户名" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.is_admin ? 'danger' : 'info'">
              {{ row.is_admin ? "管理员" : "普通用户" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="200">
          <template #default="{ row }">
            {{ row.created_at?.slice(0, 19).replace("T", " ") }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button
              type="danger"
              :icon="Delete"
              link
              :disabled="row.id === me?.id"
              @click="onDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-dialog v-model="createVisible" title="新建用户" width="420px">
      <el-form label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="createForm.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="createForm.password" type="password" show-password placeholder="初始密码" />
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch v-model="createForm.is_admin" />
          <span class="form-hint">开启后该用户可见并操作所有账号</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 800;
}

.page-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.toolbar {
  display: flex;
  justify-content: flex-end;
}

.no-perm {
  margin-top: 40px;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
