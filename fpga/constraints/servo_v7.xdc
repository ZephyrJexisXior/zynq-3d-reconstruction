## 360-degree continuous-rotation servo control (yellow signal wire only).
## Servo power must come from an independent 5-6 V supply; grounds must join.
set_property -dict {PACKAGE_PIN V7 IOSTANDARD LVCMOS33} [get_ports servo_pwm]
set_property DRIVE 8 [get_ports servo_pwm]
set_property SLEW SLOW [get_ports servo_pwm]
