/**
 * Node ESM resolver hook: let `import './engine'` find `./engine.ts`.
 *
 * Node >=23 strips TypeScript types natively, so the SME UI-math modules can
 * be imported and asserted from a plain script with no bundler and no test
 * framework. The one thing Node will not do is guess an extension — bundlers
 * do that, Node does not — so this adds `.ts` for relative specifiers that
 * carry none. Nothing else is touched.
 *
 * Used by `npm run test:ui-math` (scripts/sme_ui_math.mjs).
 */
import { registerHooks } from 'node:module'

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && !/\.[cm]?[jt]sx?$/.test(specifier)) {
      try {
        return next(`${specifier}.ts`, context)
      } catch {
        /* not a .ts module — fall through to Node's own resolution */
      }
    }
    return next(specifier, context)
  },
})
