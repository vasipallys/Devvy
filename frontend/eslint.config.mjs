import eslint from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'release/**', 'node_modules/**', '*.tsbuildinfo'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [eslint.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['electron/**/*.cjs'],
    languageOptions: { sourceType: 'commonjs', globals: globals.node },
    rules: { ...eslint.configs.recommended.rules },
  },
)
