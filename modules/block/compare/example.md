::: compare {labels = ["Before", "After"]}
```python
for item in items:
    result.append(slow_lookup(item))
```
```python
result = batch_lookup(items)
```
:::

:::: compare
::: pane Option A — keep the cron job
Simple, already deployed; misses events that happen between runs.
:::
::: pane Option B — stream changes
Catches everything in real time; one more service to run.
:::
::::
