import { cpSync, mkdirSync, existsSync, writeFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')
const src = join(root, 'frontend', 'dist')
const dest = join(root, 'dist_package', 'frontend')

if (!existsSync(src)) {
  console.error('Missing frontend/dist. Run npm run build (in frontend) first.')
  process.exit(1)
}

mkdirSync(join(root, 'dist_package'), { recursive: true })
cpSync(src, dest, { recursive: true })

const readme = `此目录由 npm run package 生成。
- frontend/  可随压缩包分发的静态资源（也可由 FastAPI 从 frontend/dist 直接托管）
- 启动含前端的 API：先在项目根执行 frontend 下的 npm run build，再 python api/main.py
`
writeFileSync(join(root, 'dist_package', 'README.txt'), readme, 'utf8')
console.log('Packaged →', dest)
