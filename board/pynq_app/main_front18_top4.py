"""18 OV5640 stills plus four concurrent USB-top patches, 5MP per camera.

Motion and image processing are retained. Concurrency is software frame
selection at a stopped position; sensor exposures are not hardware triggered.
"""
import argparse
import json
from pathlib import Path
from main_ov5640_only import capture_plan as front_plan

APP_ROOT = Path(__file__).resolve().parents[1]


def capture_plan():
    plan = front_plan()
    plan.update(schema='qian-front18-top4-plan/v1',
        move_duration_s=.58, move_durations_s=[.58] * 18, total_motion_s=10.44,
        sampling_note='User requested 18 x 580ms; no angle feedback',
        upper_camera_enabled=True,
        upper_photos_requested=4, upper_positions=[0, 4, 9, 13],
        pair_capture='parallel', hardware_synchronized=False,
        processing='retained_PL_YCoCg_quality_FAST9_BRIEF256_mutual_Hamming',
        reconstruction_strategy='OV5640_primary_with_four_upper_patches')
    return plan


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.2.1')
    parser.add_argument('--port', type=int, default=5012)
    parser.add_argument('--control-port', type=int, default=5011)
    parser.add_argument('--usb-device', default='/dev/video0')
    parser.add_argument('--output', type=Path, default=APP_ROOT/'captures_front18_top4')
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
    if not Path(args.usb_device).exists():
        raise RuntimeError('four top photographs require USB camera: '+args.usb_device)

    import signal
    from contextlib import ExitStack
    from camera_init import keep_gpio_power_active
    from camera_runtime import _start_camera, _usable_frame, _stop_on_signal
    from ethernet_stream import UdpControlReceiver
    from front_only_capture import FrontOnlySender, FrontQualityRejected, OptionalNetwork
    from front18_top4_capture import Front18Top4Sender
    from required_usb_pairs import PairQualityRejected
    from stop_capture import run_stop_capture_loop, _require_stopped

    print('FRONT18_TOP4_CONFIG '+json.dumps(plan, sort_keys=True), flush=True)
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
        front = FrontOnlySender(OptionalNetwork(args.host, args.port), args.output/'front', plan)
        cleanup.callback(front.close)
        sender = Front18Top4Sender(front, core, args.output/'top', 18, 4,
                                  args.usb_device, 'ycocg', (0, 0, 2592, 1944))
        cleanup.callback(sender.close)
        preview = core.capture_frame(timeout=20.0)
        if core.last_quality is not None:
            sender.send_quality(0xFFFFFFFF, core.last_quality)
        sender.send(preview, width=2592, height=1944, sequence=0xFFFFFFFF,
                    telemetry=core.telemetry(), compress=True)
        print('STATIC_PREVIEW 2592x1944; FRONT18_TOP4_PARALLEL; waiting for KEY0', flush=True)
        controls = UdpControlReceiver(args.control_port)
        cleanup.callback(controls.close)
        while True:
            try:
                run_stop_capture_loop(core, sender, controls, plan['motion_command_us'],
                    _usable_frame, plan['move_duration_s'], plan['minimum_stop_s'],
                    plan['settle_before_sample_s'], plan['positions'],
                    move_durations=plan['move_durations_s'], still_acquirer=sender.acquire_still)
            except (FrontQualityRejected, PairQualityRejected) as exc:
                _require_stopped(core)
                print('QUALITY_ABORTED %s; waiting for a new KEY0; no automatic motion retry' % exc,
                      flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
