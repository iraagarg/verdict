// Flat config. The only rules that matter here are the ones that enforce
// non-negotiable #5: no `any`, no unchecked external input.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "**/dist/**",
      "**/.next/**",
      "**/node_modules/**",
      "**/coverage/**",
      "artifacts/**",
      // The evald virtualenv ships vendored JavaScript (pip bundles a urllib3
      // emscripten worker). It is not ours and must not be linted.
      "**/.venv/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      // Destructuring a value purely to drop it is the clearest way to build a
      // "same env minus one variable" fixture.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": "error",
      "no-console": ["error", { allow: ["error"] }],
    },
  },
  {
    // Tests may log and may construct deliberately-invalid values.
    files: ["**/*.test.ts", "**/*.test.tsx"],
    rules: { "no-console": "off" },
  },
);
