# Exempt asynchronous CLR/PRE pins only; keep synchronizer stages timed.
set_false_path -to [get_pins -hier -quiet -filter {NAME =~ *camera_reset*reset_sync_reg*/CLR || NAME =~ *camera_reset*reset_sync_reg*/PRE}]
# Only first-stage observation inputs are asynchronous.
set_false_path -to [get_pins -hier -quiet -filter {NAME =~ *camera_probe_sync_0*sample_meta_reg*/D}]