"""Independent OV5640 5MP entry: USB-top capture temporarily disabled.

The retained motor controller and bitstream are unchanged: 18 x 540 ms at
1612 us, neutral 1500 us, settle >=1 s and stop >=2 s. KEY0 starts a round.
"""
import argparse
import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]


def capture_plan():
    return {'schema': 'qian-ov5640-only-plan/v1', 'capture_mode': 'stop-and-shoot',
        'positions': 18, 'front_photos_requested': 18, 'upper_photos_requested': 0,
        'upper_camera_enabled': False, 'resolution': [2592, 1944],
        'signal_pin': 'V7', 'motion_command_us': 1612, 'stop_command_us': 1500,
        'move_duration_s': .54, 'move_durations_s': [.54] * 18,
        'total_motion_s': 9.72, 'minimum_stop_s': 2.0, 'settle_before_sample_s': 1.0,
        'angle_calibrated': False, 'full_turn_verified': False,
        'sampling_note': 'Existing 18 x 540ms profile retained; no angle feedback'}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.2.1')
    parser.add_argument('--port', type=int, default=5012)
    parser.add_argument('--control-port', type=int, default=5011)
    parser.add_argument('--output', type=Path, default=APP_ROOT/'captures_front18')
    parser.add_argument('--show-plan', action='store_true')
    args = parser.parse_args(argv)
    if not (1 <= args.port <= 65535 and 1 <= args.control_port <= 65535):
        parser.error('ports must be 1..65535')
    return args


def run(argv=None):
    args = parse_args(argv)
    plan = capture_plan()
    if args.show_plan:
        print(json.dumps(plan, indent=2))
        return 0
    import signal
    from contextlib import ExitStack
    from camera_init import keep_gpio_power_active
    from camera_runtime import _start_camera, _usable_frame, _stop_on_signal
    from ethernet_stream import UdpControlReceiver
    from front_only_capture import FrontOnlySender, FrontQualityRejected, OptionalNetwork
    from stop_capture import run_stop_capture_loop, _require_stopped

    print('OV5640_ONLY_CONFIG ' + json.dumps(plan, sort_keys=True), flush=True)
    with ExitStack() as cleanup:
        previous_handler = signal.signal(signal.SIGTERM, _stop_on_signal)
        cleanup.callback(signal.signal, signal.SIGTERM, previous_handler)
        cleanup.enter_context(keep_gpio_power_active())
        core = _start_camera('ycocg', '2592x1944')
        cleanup.callback(core.stop)
        if not core.has_stop_capture:
            raise RuntimeError('retained stop-and-shoot overlay is required')
        _require_stopped(core)
        core.set_roi(0, 0, 2592, 1944)
        sender = FrontOnlySender(OptionalNetwork(args.host, args.port), args.output, plan)
        cleanup.callback(sender.close)
        preview = core.capture_frame(timeout=20.0)
        if core.last_quality is not None:
            sender.send_quality(0xFFFFFFFF, core.last_quality)
        sender.send(preview, width=2592, height=1944, sequence=0xFFFFFFFF,
                    telemetry=core.telemetry(), compress=True)
        print('STATIC_PREVIEW 2592x1944; USB_TOP_DISABLED; waiting for KEY0', flush=True)
        controls = UdpControlReceiver(args.control_port)
        cleanup.callback(controls.close)
        while True:
            try:
                run_stop_capture_loop(core, sender, controls, plan['motion_command_us'],
                    _usable_frame, plan['move_duration_s'], plan['minimum_stop_s'],
                    plan['settle_before_sample_s'], plan['positions'],
                    move_durations=plan['move_durations_s'])
            except FrontQualityRejected as exc:
                # The unchanged loop's finally block stops/disarms motion and
                # marks this round incomplete. A NEW KEY0 is required to retry.
                _require_stopped(core)
                print('FRONT_QUALITY_REJECTED %s; waiting for a new KEY0' % exc, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
