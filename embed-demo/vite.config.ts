import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(() => {
  const root = path.resolve(__dirname, ".");

  return {
    root,
    envDir: root,
  };
});
