# Shirone-Strapi

基于 `shirones@0.1.5` 的 Astro 混合渲染站点，使用 Strapi 5 管理文章并由 Meilisearch 提供搜索。内容路由从服务端读取已发布内容，后台编辑后不需要重新构建站点。目前已部署到 `hyperbola.cc`。

## 本地开发和验证

使用支持的 Node.js 版本和 pnpm，依照锁文件安装依赖。站点需要能访问 Strapi；`LIVE_PROBE_FILE` 只用于隔离测试，不能用于生产。

```sh
pnpm install --frozen-lockfile
pnpm build
pnpm test
pnpm --dir cms install --frozen-lockfile
pnpm --dir cms test
```

Strapi 所需的数据库、密钥和渲染服务变量见 [cms/.env.example](cms/.env.example)；站点需要 `STRAPI_URL`、仅供服务端使用的 `STRAPI_API_TOKEN`、`MEILI_URL`、搜索专用 `MEILI_SEARCH_KEY` 和 `INTERNAL_RENDER_TOKEN`。实际密钥由受保护的部署配置提供，禁止写入 Git。

## 内容与安全

Strapi 以 Markdown 和原 frontmatter 保存内容；仅发布的内容会被页面读取。保存时使用受认证的内部渲染服务，服务暂不可用时清除旧快照并在请求时重新渲染。动态内容的 include 只读取已发布的 Strapi 片段，不读取服务器本地文件。搜索索引排除加密和草稿内容，搜索结果及上传资源会重新核对当前发布状态。不要将 Strapi 的整个上传目录直接公开。

旧站内容迁移入口及 dry-run 说明在 [cms/README.md](cms/README.md)；生产部署、回退和数据恢复说明见 [deploy/k8s/README.md](deploy/k8s/README.md) 与 [deploy/k8s/RECOVERY.md](deploy/k8s/RECOVERY.md)。按站点所有者要求，当前没有自动应用备份。

Shirone 的路由和运行时适配保存在 [patches/shirones@0.1.5.patch](patches/shirones@0.1.5.patch)，升级主题时需重新验证补丁。中文字体按 Unicode 范围分片，生成方法见 [字体说明](src/assets/fonts/yozai-shards/README.md)。

### 受保护媒体浏览器回归

`pnpm test:browser` 使用本地 Chromium 和 Playwright 验证浏览器解密、HttpOnly Cookie、图片显示，以及改密和撤回后的拒绝访问。它仅允许 `127.0.0.1`，并阻断其他网络请求；测试凭据仅为虚拟数据。

先在 `/tmp` 下准备项目的隔离副本，将副本 `shirones/config/siteConfig.ts` 中的 `site` 设置为与测试端口一致的 HTTP loopback 地址（例如 `http://127.0.0.1:43888/`），并在该副本运行 `pnpm build`。随后从项目目录运行：

```sh
BROWSER_E2E_ROOT=/tmp/已构建副本 \
BROWSER_E2E_ORIGIN=http://127.0.0.1:43888 \
PLAYWRIGHT_MODULE=/Playwright/index.mjs的绝对路径 \
CHROMIUM_EXECUTABLE=/Chromium可执行文件的绝对路径 \
pnpm test:browser
```

需事先提供兼容的本地 Playwright 和 Chromium；该回归不会下载浏览器。结果写入隔离副本的 `evidence.json`。正式 HTTPS 的 Secure Cookie 属性另由 `pnpm test` 验证。
