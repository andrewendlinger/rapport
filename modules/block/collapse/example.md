::: collapse How we measured
Each request was replayed 1,000 times against a warm cache.

```bash
wrk -t4 -c64 -d30s http://localhost:8080/items
```
:::
