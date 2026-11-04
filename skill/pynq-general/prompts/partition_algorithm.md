# Prompt Workflow: PL/PS Partitioning

Copy this prompt and replace the bracketed inputs. Attach the algorithm description, data format, candidate board, and measured constraints.

> Analyze `[algorithm/task]` for implementation on `[exact FPGA/SoC and PYNQ version]`.
>
> Inputs: `[format, dimensions, rate, synchronization]`  
> Outputs: `[format, tolerance, latency target]`  
> Available evidence: `[RTL/IP, utilization, timing, software reference, memory bandwidth]`
>
> Produce a table with each stage, computation, state/memory needed, throughput, latency, precision, and recommended PL/PS ownership. Separate facts from assumptions. Identify data movement and cache-coherency requirements. Give one small deterministic test vector and a golden-reference comparison plan. Do not claim feasibility without resource/timing evidence. List the experiment that would falsify the proposed split.

## Applicability

Use during architecture selection or when moving an existing software stage into PL. It is useful across workloads but needs actual input rates, precision requirements, device resources, and tool reports.

## Failure conditions

Do not use the generated recommendation as a resource estimate when the device/clock/throughput constraints are unknown. The prompt cannot replace HLS/RTL implementation, synthesis, timing closure, or board measurements.
