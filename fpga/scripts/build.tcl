# Build the published 5MP/dual-view topology in a new, isolated workspace.
# This script never loads hardware or changes any capture/motion parameters.
set repo_root [file normalize [file join [file dirname [info script]] ../..]]
set build_root [file join $repo_root build fpga]
if {[llength $argv]} {set build_root [file normalize [lindex $argv 0]]}
set project_dir [file join $build_root project]
if {[file exists [file join $project_dir qian_vision.xpr]]} {
    error "Build project already exists; use a new output directory. No files were removed."
}
foreach relative {
    fpga/source/ip_repo/ov5640_capture_data/component.xml
    fpga/source/ip_repo/ov5640_capture_data/src/ov5640_capture_data.v
    fpga/source/ip_repo/rgb2lcd/ip/component.xml
    fpga/source/ip_repo/rgb2lcd/ip/src/rgb2lcd.v
} {
    if {![file isfile [file join $repo_root $relative]]} {
        error "Missing authorized local IP dependency: $relative. See docs/FPGA_BUILD.md."
    }
}
if {[version -short] ne "2026.1"} {
    error "The published BD targets Vivado 2026.1; upgrade/migration needs separate validation."
}
file mkdir $build_root
create_project qian_vision $project_dir -part xc7z020clg400-2
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set rtl_root [file join $repo_root fpga source ip_repo]
set_property ip_repo_paths [list $rtl_root] [current_project]
update_ip_catalog
set rtl [concat [glob -nocomplain [file join $rtl_root *.v]] \
                [glob -nocomplain [file join $rtl_root image_process *.v]] \
                [glob -nocomplain [file join $rtl_root dual_view *.v]]]
add_files -norecurse $rtl
add_files -fileset constrs_1 -norecurse [glob [file join $repo_root fpga constraints *.xdc]]
update_compile_order -fileset sources_1
# Preserve the relative depth recorded in BD gen_directory so all generated
# products stay within build_root/source, rather than the published sources.
set bd_dir [file join $build_root source ov5640_lcd.srcs sources_1 bd system]
file mkdir $bd_dir
set bd [file join $bd_dir system.bd]
file copy [file join $repo_root fpga source system.bd] $bd
add_files -norecurse $bd
open_bd_design $bd
validate_bd_design
save_bd_design
generate_target all [get_files $bd]
set wrapper [make_wrapper -files [get_files $bd] -top]
add_files -norecurse $wrapper
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {error "Synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {error "Implementation failed"}
open_run impl_1
set reports [file join $build_root reports]
file mkdir $reports
report_timing_summary -file [file join $reports timing.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_drc -file [file join $reports drc.rpt]
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} {error "Timing paths missing"}
if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} {error "Timing failed"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]]} {
    error "DRC failed; do not deploy this build"
}
# Locate only handoff files created inside this dedicated build tree.
proc find_hwh {directory} {
    set found [glob -nocomplain -types f -directory $directory *.hwh]
    foreach child [glob -nocomplain -types d -directory $directory *] {
        set found [concat $found [find_hwh $child]]
    }
    return $found
}
set hw_files [find_hwh $build_root]
if {[llength $hw_files] != 1} {error "Expected exactly one generated HWH: $hw_files"}
set artifacts [file join $build_root artifacts]
file mkdir $artifacts
file copy [file join [get_property DIRECTORY [get_runs impl_1]] system_wrapper.bit] \
          [file join $artifacts camera_5mp_quality.bit]
file copy [lindex $hw_files 0] [file join $artifacts camera_5mp_quality.hwh]
write_debug_probes -force [file join $artifacts camera_5mp_quality.ltx]
puts "SOURCE_BUILD_COMPLETE; hardware validation still required; $artifacts"
close_project
exit 0
