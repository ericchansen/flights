// ESLint flat config for the offline fare-map front-end.
//
// Correctness-focused: the recommended rules catch real bugs (undeclared names,
// unreachable code, accidental globals). Vendored libraries and generated data
// are excluded; formatting is delegated to Prettier.
import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: ["public/vendor/**", "public/data.json", "node_modules/**", "coverage/**"],
  },
  js.configs.recommended,
  {
    // Browser code: the app entry and the pure modules it imports.
    files: ["public/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        // Vendored globals loaded via classic <script> before the module.
        d3: "readonly",
        topojson: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // Node context: Vitest specs and config files.
    files: ["test/**/*.js", "*.config.js", "eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
  },
];
