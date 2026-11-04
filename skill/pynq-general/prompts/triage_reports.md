# Prompt Workflow: Timing and Resource Triage

Use with the complete synthesis/implementation reports and exact source revision.

> Review the attached Vivado utilization, timing summary, DRC, and build log for `[device part]` using `[Vivado version]`.
>
> 1. Quote the exact failing path/resource rows and identify whether the evidence is post-synthesis or post-implementation.
> 2. Rank likely causes by evidence, separating a measured fact from a hypothesis.
> 3. For each proposed fix, name the expected metric change and a regression check (golden output, resource budget, timing, or board behavior).
> 4. Check clock constraints, unconstrained paths, generated clocks, CDC boundaries, fanout, BRAM/DSP/LUT pressure, and AXI backpressure where relevant.
> 5. State which conclusion cannot be reached from the supplied reports.
>
> Do not infer a safe clock from the fastest path alone. Do not recommend relaxing a constraint without explaining the external interface requirement it changes.

## Applicability

Use for report review after a reproducible synthesis/implementation run. Attach the raw report files; a screenshot or copied summary alone may omit the path and constraint context.

## Failure conditions

The workflow is incomplete when timing constraints, exact device, tool version, or implementation status are missing. It diagnoses evidence; it does not make RTL changes or prove board-level function.
