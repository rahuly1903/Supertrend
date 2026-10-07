# Web (Next.js)

Read-only frontend + API for the scanner. See the repository README for setup, pages and API.

```bash
npm install            # also runs prisma generate
npm run dev            # http://localhost:3000 (needs DATABASE_URL in web/.env)
npm run build && npm start
npm run prisma:check   # print the live DB schema to diff against prisma/schema.prisma
```
