::: plantuml {caption = "Reconstruction check"}
@startuml
start
:Acquire k-space;
:Inverse FFT;
if (SNR ok?) then (yes)
  :Store image;
else (no)
  :Flag for rescan;
endif
stop
@enduml
:::
