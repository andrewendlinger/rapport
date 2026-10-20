```python {title = "cache.py", hl = [3]}
def get(key):
    if key in _cache:
        return _cache[key]   # highlighted line
    return load(key)
```
