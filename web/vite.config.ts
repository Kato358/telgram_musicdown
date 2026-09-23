import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      $lib: fileURLToPath(new URL("./src/lib", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8787",
    },
  },
  preview: {
    proxy: {
      "/api": "http://127.0.0.1:8787",
    },
  },
});
