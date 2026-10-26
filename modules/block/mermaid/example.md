::: mermaid {caption = "Scan to report"}
flowchart LR
  A[Scanner] --> B[Reconstruction]
  B --> C{QC passed?}
  C -->|yes| D[Metrics report]
  C -->|no| A
:::
