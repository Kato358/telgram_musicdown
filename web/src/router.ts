import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/setup", name: "setup", component: () => import("@/views/SetupView.vue") },
    { path: "/", redirect: "/dashboard" },
    { path: "/dashboard", name: "dashboard", component: () => import("@/views/DashboardView.vue") },
    { path: "/search", name: "search", component: () => import("@/views/SearchView.vue") },
    { path: "/tasks", name: "tasks", component: () => import("@/views/TasksView.vue") },
    { path: "/history", name: "history", component: () => import("@/views/HistoryView.vue") },
    { path: "/sources", name: "sources", component: () => import("@/views/SourcesView.vue") },
    { path: "/settings", name: "settings", component: () => import("@/views/SettingsView.vue") },
    { path: "/logs", name: "logs", component: () => import("@/views/LogsView.vue") },
  ],
});
