install:
	pnpm install

build:
	pnpm build

dev:
	pnpm dev

test:
	pnpm test

test-e2e:
	pnpm test:e2e

lint:
	pnpm lint

# 静态门禁（2026-10-08 CI 优化批 A1）：lint-check.yml frontend job 逐字复用本 target，
# 与 CI 单一事实源——knip/i18n 失败落 Lint Check 而非 Frontend Unit Tests。
check:
	pnpm check
	pnpm check:dead
	node scripts/check_i18n_keys.mjs

format:
	pnpm format:write
