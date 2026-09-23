import js from "@eslint/js";
import globals from "globals";
import ts from "typescript-eslint";
import svelte from "eslint-plugin-svelte";

export default ts.config(
  { ignores: ["dist/**", "node_modules/**", "src/lib/components/ui/**"] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["**/*.svelte", "**/*.svelte.ts", "**/*.svelte.js"],
    languageOptions: {
      parserOptions: {
        parser: ts.parser,
      },
    },
  },
);
