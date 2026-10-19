import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to `server.cli serve` on :8000; `npm run build`
// writes dist/, which the server serves at /.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/video": "http://127.0.0.1:8000",
      "/chat": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
