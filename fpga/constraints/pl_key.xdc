# Baseboard KEY0 is active low on FPGA L14.
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33 PULLUP true} [get_ports {pl_key[0]}]
# Exempt only the asynchronous input to the first synchronizer stage.
set_false_path -from [get_ports {pl_key[0]}] -to [get_pins -hier -quiet -filter {NAME =~ *key_capture/key_meta_reg/D}]
