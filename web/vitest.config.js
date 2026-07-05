import { defineConfig } from "vitest/config";

// The extracted modules under test are pure (no DOM), so the default Node
// environment is sufficient — no jsdom needed.
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.js"],
  },
});
