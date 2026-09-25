# Inventory System Web

库存管理系统前端，使用 Next.js App Router、React、TypeScript 和 Ant Design，通过 FastAPI 提供业务 API。

```bash
pnpm dev
pnpm build
```

`pnpm build` 使用 Next.js 静态导出并生成 `web/out/`。API 地址由 `NEXT_PUBLIC_API_BASE_URL` 配置；生产环境推荐留空，通过同源 `/api` 访问后端。
