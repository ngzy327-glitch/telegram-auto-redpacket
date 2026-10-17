<script setup lang="ts">
import { reactive, ref } from "vue"
import { useRouter, useRoute } from "vue-router"
import { Lock, Present, User } from "@element-plus/icons-vue"

import { api } from "@/api"
import { clearAuth, getUser } from "@/lib/auth"

const router = useRouter()
const route = useRoute()

const form = reactive({ username: "", password: "" })
const loading = ref(false)

async function onSubmit() {
  if (!form.username || !form.password) {
    ElMessage.warning("请输入用户名和密码")
    return
  }
  loading.value = true
  try {
    const user = await api.login(form.username, form.password)
    ElMessage.success(`欢迎回来，${user.username}`)
    const redirect = (route.query.redirect as string) || "/dashboard"
    router.replace(redirect)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

// 如果已经登录，直接跳走
const existing = getUser()
if (existing) {
  // 轻量校验 token 是否还有效
  api.me().then(() => router.replace("/dashboard")).catch(() => clearAuth())
}
</script>

<template>
  <div class="login-page">
    <div class="login-card chunky-shadow">
      <div class="brand">
        <div class="brand-logo">
          <el-icon :size="22"><Present /></el-icon>
        </div>
        <div class="brand-text">
          <span class="brand-name">浅秋科技v1</span>
          <span class="brand-sub">红包系统</span>
        </div>
      </div>

      <h2 class="title">登录控制台</h2>
      <p class="subtitle">每个用户只能看到和操作自己的 Telegram 账号</p>

      <el-form @submit.prevent="onSubmit" class="form">
        <el-form-item>
          <el-input
            v-model="form.username"
            placeholder="用户名"
            size="large"
            :prefix-icon="User"
            @keyup.enter="form.password && onSubmit()"
          />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            size="large"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          class="submit"
          :loading="loading"
          @click="onSubmit"
        >
          登 录
        </el-button>
      </el-form>

      <p class="foot-tip">
        管理员默认账号 <b>admin</b>，密码在服务端 <code>.env</code> 的
        <code>ADMIN_PASSWORD</code> 中配置。
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: var(--el-bg-color-page);
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border: 3px solid #000;
  border-radius: 16px;
  padding: 36px 32px 28px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 24px;
}

.brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid #000;
  background: var(--memphis-orange);
  color: #fff;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand-name {
  font-weight: 700;
  font-size: 16px;
}

.brand-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.title {
  margin: 0;
  font-size: 22px;
  font-weight: 800;
}

.subtitle {
  margin: 6px 0 24px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.form {
  margin-top: 8px;
}

.submit {
  width: 100%;
  margin-top: 4px;
  font-weight: 700;
}

.foot-tip {
  margin: 20px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  text-align: center;
}

.foot-tip code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--el-fill-color-light);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
