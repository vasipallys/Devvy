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
      // React treats an effect's return value as its cleanup function. A concise arrow body
      // returns whatever the expression evaluates to, so anything non-callable there crashes
      // the whole tree with "destroy is not a function" on the next re-run or unmount.
      // TypeScript does not catch this: EffectCallback permits `void`, and DOM methods like
      // scrollIntoView are typed `void` regardless of what they return at runtime.
      'no-restricted-syntax': ['error', {
        selector: "CallExpression[callee.name='useEffect'] > ArrowFunctionExpression[body.type!='BlockStatement']",
        message: 'useEffect callbacks must use a block body: a concise arrow returns its expression, which React calls as the cleanup function.',
      }],
    },
  },
)
