<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  ChatLineSquare,
  CircleClose,
  DataLine,
  Expand,
  Fold,
  Grid,
  Monitor,
  Odometer,
  Present,
  Setting,
  SwitchButton,
  User,
  UserFilled,
} from "@element-plus/icons-vue"

import { api } from "@/api"
import { clearAuth, getUser, type WebUser } from "@/lib/auth"
import type { Overview } from "@/types"
import { usePolling } from "@/composables/usePolling"

const route = useRoute()
const router = useRouter()
const isMobile = ref(false)
const collapsed = ref(false)
const drawerOpen = ref(false)

function syncViewport() {
  const mobile = window.innerWidth < 768
  isMobile.value = mobile
  if (mobile) {
    collapsed.value = true
    drawerOpen.value = false
  } else {
    drawerOpen.value = false
  }
}

function toggleSidebar() {
  if (isMobile.value) {
    drawerOpen.value = !drawerOpen.value
    collapsed.value = false
  } else {
    collapsed.value = !collapsed.value
  }
}

onMounted(() => {
  syncViewport()
  window.addEventListener("resize", syncViewport)
})
onUnmounted(() => window.removeEventListener("resize", syncViewport))

const me: WebUser | null = getUser()

const NAV = [
  { index: "/dashboard", label: "总览", icon: Odometer, adminOnly: false },
  { index: "/accounts", label: "账号管理", icon: User, adminOnly: false },
  { index: "/modules", label: "红包模块", icon: Grid, adminOnly: false },
  { index: "/groups", label: "秒包管理", icon: ChatLineSquare, adminOnly: false },
  { index: "/blocklist", label: "屏蔽管理", icon: CircleClose, adminOnly: false },
  { index: "/records", label: "秒包记录", icon: DataLine, adminOnly: false },
  { index: "/webapp-logs", label: "WebApp 日志", icon: Monitor, adminOnly: false },
  { index: "/users", label: "用户管理", icon: UserFilled, adminOnly: true },
  { index: "/settings", label: "系统配置", icon: Setting, adminOnly: true },
]

const visibleNav = computed(() =>
  NAV.filter((n) => !n.adminOnly || me?.is_admin)
)

const activeIndex = computed(() => "/" + (route.path.split("/")[1] || "dashboard"))
const title = computed(() => (route.meta.title as string) || "")

function onSelect(index: string) {
  if (isMobile.value) drawerOpen.value = false
  router.push(index)
}

function logout() {
  clearAuth()
  router.replace("/login")
}

// 顶栏全局状态（轻量轮询，切到后台自动暂停）
const ov = ref<Overview | null>(null)
async function loadStatus() {
  try {
    ov.value = await api.getOverview()
  } catch {
    /* 静默：后端重启窗口期忽略 */
  }
}
usePolling(loadStatus, 10000)
</script>

<template>
  <el-container class="layout">
    <div v-if="isMobile && drawerOpen" class="mask" @click="drawerOpen = false" />
    <el-aside
      :width="collapsed ? '72px' : '224px'"
      class="aside"
      :class="{ 'aside--mobile': isMobile, 'aside--drawer-open': drawerOpen }"
    >
      <div class="brand">
        <div class="brand-logo chunky-shadow-sm">
          <el-icon :size="20"><Present /></el-icon>
        </div>
        <div v-if="!collapsed" class="brand-text">
          <span class="brand-name">浅秋科技v1</span>
          <span class="brand-sub">红包系统</span>
        </div>
      </div>

      <el-menu
        :default-active="activeIndex"
        :collapse="collapsed"
        :collapse-transition="false"
        class="menu"
        @select="onSelect"
      >
        <el-menu-item v-for="item in visibleNav" :key="item.index" :index="item.index">
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.label }}</template>
        </el-menu-item>
      </el-menu>

      <div v-if="!collapsed" class="aside-footer">
        <span class="version-pill">v1.0.0</span>
      </div>
    </el-aside>

    <el-container>
      <el-header class="header">
        <el-button text class="collapse-btn" @click="toggleSidebar">
          <el-icon :size="18">
            <Expand v-if="collapsed" />
            <Fold v-else />
          </el-icon>
        </el-button>
        <el-divider direction="vertical" />
        <span class="header-title">{{ title }}</span>

        <div class="header-right">
          <div class="header-status" v-if="ov">
            <span class="hs-pill">
              <span class="hs-dot hs-dot--running" />
              运行 <b class="tnum">{{ ov.accounts.running }}</b
              >/<span class="tnum">{{ ov.accounts.total }}</span>
            </span>
            <span class="hs-pill">
              今日成功 <b class="tnum">{{ ov.today.success }}</b>
              <span class="hs-muted">/{{ ov.today.total }}</span>
            </span>
            <span class="hs-pill">
              福利来池
              <b class="tnum">{{ ov.pool.available }}</b
              ><span class="hs-muted">/{{ ov.pool.pool_size }}</span>
            </span>
          </div>

          <el-dropdown trigger="click" @command="(c: string) => c === 'logout' && logout()">
            <span class="user-chip">
              <el-icon :size="16"><UserFilled /></el-icon>
              <span class="user-name">{{ me?.username }}</span>
              <el-tag v-if="me?.is_admin" size="small" type="danger" effect="plain">
                管理员
              </el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout" :icon="SwitchButton">
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <div class="main-inner">
          <router-view v-slot="{ Component }">
            <keep-alive>
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}

.aside {
  display: flex;
  flex-direction: column;
  border-right: 3px solid #000;
  background: var(--el-bg-color);
  transition: width 0.2s;
  overflow: hidden;
  flex-shrink: 0;
}

/* 移动端抽屉 */
.aside--mobile {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 1001;
  height: 100vh;
  transform: translateX(-100%);
  transition: transform 0.25s ease;
}

.aside--drawer-open {
  transform: translateX(0);
}

.mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  z-index: 1000;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 16px;
  border-bottom: 2px solid #000;
  flex-shrink: 0;
}

.brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 2px solid #000;
  background: var(--memphis-orange);
  color: #fff;
  flex-shrink: 0;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  white-space: nowrap;
}

.brand-name {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.01em;
}

.brand-sub {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.menu {
  flex: 1;
  border-right: none;
  padding: 10px 12px;
  --el-menu-item-height: 44px;
}

/* 胶囊式导航项 */
.menu :deep(.el-menu-item) {
  border-radius: 8px;
  margin-bottom: 6px;
  font-weight: 600;
  border: 2px solid transparent;
  transition:
    background-color 0.12s,
    border-color 0.12s,
    box-shadow 0.12s;
}

.menu :deep(.el-menu-item:hover) {
  background: var(--memphis-yellow);
  border-color: #000;
}

.menu :deep(.el-menu-item.is-active) {
  background: var(--memphis-orange);
  color: #fff;
  border-color: #000;
  box-shadow: var(--chunky-shadow);
  font-weight: 700;
}

.menu.el-menu--collapse {
  padding: 10px 8px;
}

.menu.el-menu--collapse :deep(.el-menu-item) {
  border-radius: 8px;
}

.aside-footer {
  padding: 14px 16px;
  border-top: 2px solid #000;
}

.version-pill {
  display: inline-block;
  padding: 2px 12px;
  font-size: 11px;
  font-weight: 700;
  background: var(--memphis-yellow);
  border: 2px solid #000;
  border-radius: 6px;
  box-shadow: var(--chunky-shadow-sm);
  transform: rotate(-2deg);
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 64px;
  border-bottom: 3px solid #000;
  background: var(--el-bg-color);
  flex-shrink: 0;
}

.collapse-btn {
  padding: 6px;
}

.header-title {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-left: auto;
}

.header-status {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 2px solid #000;
  border-radius: 999px;
  background: var(--memphis-yellow);
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  box-shadow: var(--chunky-shadow-sm);
  transition: transform 0.1s;
}

.user-chip:hover {
  transform: translateY(-1px);
}

.user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 顶栏状态胶囊：统一白底（信息分类不靠色相，状态点才用色） */
.hs-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border: 2px solid #000;
  border-radius: 999px;
  font-weight: 600;
  background: #fff;
  box-shadow: var(--chunky-shadow-sm);
  white-space: nowrap;
}

.hs-pill b {
  font-weight: 700;
}

.hs-muted {
  color: rgba(0, 0, 0, 0.45);
}

.hs-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid #000;
  margin-right: 2px;
}

.hs-dot--running {
  background: var(--el-color-success);
}

.main {
  background: var(--el-bg-color-page);
  padding: 24px;
  overflow-x: hidden;
}

.main-inner {
  max-width: 1320px;
  margin: 0 auto;
}

@media (max-width: 768px) {
  .header {
    height: 56px;
    padding: 0 8px;
  }
  .header-status {
    display: none;
  }
  .user-name {
    display: none;
  }
  .main {
    padding: 12px;
  }
  .header-title {
    font-size: 15px;
  }
}
</style>
