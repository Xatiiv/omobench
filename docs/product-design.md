# OmoBench product interface

The website and local panels share a midnight navy and amber palette. Amber identifies primary actions and output throughput; blue distinguishes secondary telemetry. Status labels always accompany colour.

| Role | Colour |
| --- | --- |
| Page background | `#090f1c` |
| Panel surface | `#111c2e` |
| Primary accent | `#f7bd55` |
| Secondary measurement | `#7da9f7` |
| Main text | `#edf3fc` |
| Supporting text | `#a5b6cf` |

The hosted website introduces the benchmark and presents registry evidence. The local guided panel runs the existing CLI. A link to the advanced panel remains available, and the CLI's workload, runner arguments, profiles and acceptance rules are unchanged.

## Live performance display

The main readout shows the runner's cumulative output-token throughput. First-token latency and request time are live means from the existing telemetry snapshot. The graph uses only finite, non-negative observed samples, plotted against elapsed seconds. Its range control lets pointer and keyboard users inspect a sample. A single observed sample is visible as a point. No composite score or hardware utilisation is inferred.

Request counters cover all run phases. They are explicitly labelled to distinguish them from the final measured-window metrics, which exclude the profile's warmup and cooldown. The final report continues to use normalized result metrics, including its median and percentile latency values.

Missing telemetry displays Pending rather than a manufactured zero. Failed or cancelled runs retain their last actual progress. Evidence finalization stays below 100%; only successful execution and evidence finalization may show 100%. The duration-overrun message and ETA behavior are preserved.

## Interaction and accessibility

- Controls retain visible keyboard focus and meaningful labels.
- The chart includes axis units, a text readout and an accessible range control.
- Layouts collapse to one column on narrow screens without hiding essential measurements or cancellation controls.
- Reduced-motion preferences disable decorative transitions and indeterminate animation.
- Website illustration elements describe the model/runtime/hardware relationship and do not claim measured performance.
- Public download links remain absent until release metadata identifies a published installer.

Use commas or other ordinary punctuation in interface and documentation prose instead of long sentence dashes. Keep command flags and numeric ranges intact.
