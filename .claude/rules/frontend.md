---
paths:
  - "frontend/src/**/*.ts"
  - "frontend/src/**/*.tsx"
---

# Frontend Architecture Rules

- **Pages** (`src/app/**/page.tsx`): Composition only — fetch data, compose components, handle routing.
- **Components** (`src/components/**`): Stateless/low-state UI, receive data via props.
- **State**: Local React state for component-only concerns. Shared hooks in `src/store/` or `src/lib/` for cross-page state.
- **HTTP**: All requests through `src/lib/api.ts` (typed Axios instance). Never use `fetch` or `axios` directly in components.
- **Types**: Reuse shared types from `src/lib/types.ts`. Type all props and exported functions.
- **i18n**: All user-facing strings use the `useI18n()` hook. Never hardcode UI text.
