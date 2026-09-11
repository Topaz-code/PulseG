import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * The frontend test runner.
 *
 * Separate from vite.config.ts on purpose: the app config is what ships, and adding test-only keys
 * to it is how a production build ends up carrying test settings. The alias is repeated rather
 * than imported so this file cannot break the build if the other one changes shape.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["./src/test/setup.ts"],
    globals: false,
    // Generous: the first render of a view waits on several queries, and a cold Vite transform
    // of the whole component tree is the slowest thing in the run.
    testTimeout: 20_000,
    restoreMocks: true,
    css: false,
  },
});
