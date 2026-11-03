"""KEY0 starts bounded hardware moves with neutral-only still photographs."""
import json
import time

from fpga_control import (FULL_5MP_HEIGHT, FULL_5MP_WIDTH, KEY_ROTATING,
                          SERVO_ACTIVE_US, quality_frame_is_complete)

BUSY = KEY_ROTATING | 4


def _wait_until(deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.01, remaining))


def _require_stopped(core):
    trigger, status, frame = core.button_capture_state()
    if status & BUSY or core.mmio.read(SERVO_ACTIVE_US) != 1500:
        raise RuntimeError("still photo requires neutral PWM and no active/pending move")
    return trigger


def _wait_short_stop(core, expected_trigger, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        trigger, status, frame = core.button_capture_state()
        if trigger != expected_trigger:
            raise RuntimeError("unexpected rotation trigger during short move")
        if not status & BUSY and core.mmio.read(SERVO_ACTIVE_US) == 1500:
            return time.monotonic()
        time.sleep(0.002)
    raise TimeoutError("short move did not return to neutral")


def _wait_new_trigger(core, previous, timeout=1.0):
    deadline = time.monotonic() + timeout
    expected = (previous + 1) & 0xFFFFFFFF
    while time.monotonic() < deadline:
        trigger, status, frame = core.button_capture_state()
        if trigger == expected:
            return trigger
        if trigger != previous:
            raise RuntimeError("unexpected trigger count during software move")
        time.sleep(0.002)
    raise TimeoutError("FPGA did not accept the short move")


def _fresh_still_frame(core, usable_frame, timeout=3.0):
    _require_stopped(core)
    # Skip stores exposed before the still-photo sampling point. Requiring
    # two frame advances also covers the input/ISP/DDR pipeline boundary.
    # QUALITY_FRAME counts completed output frames. FRAME_COUNT counts an
    # earlier SOF stage; switching front/DDR sources can increment that stage
    # without completing a quality frame. Never subtract these two counters.
    # Always anchor at the current output count, even after restore_front has
    # deliberately cleared last_quality.
    quality_reader = getattr(core, 'quality', None)
    previous_quality = quality_reader() if callable(quality_reader) else core.last_quality
    if previous_quality is None:
        raise RuntimeError('cannot anchor still sampling without output quality frame')
    anchor = previous_quality['frame']
    diagnostic={'anchor_quality_frame':anchor,'capture_calls':0}
    core.last_still_diagnostic=diagnostic
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = core.capture_frame(timeout=max(0.01, deadline-time.monotonic()))
        diagnostic['capture_calls']+=1
        _require_stopped(core)
        frame_height, frame_width = frame.shape[:2]
        expected_width = getattr(core, "width", frame_width)
        expected_height = getattr(core, "height", frame_height)
        require_shape = (expected_width >= FULL_5MP_WIDTH or
                         expected_height >= FULL_5MP_HEIGHT)
        quality = core.last_quality
        if quality is None or not quality_frame_is_complete(
                quality, expected_width, expected_height,
                require_shape=require_shape):
            continue
        advance = (quality["frame"] - anchor) & 0xFFFFFFFF
        if 2 <= advance < 0x80000000 and usable_frame(frame):
            diagnostic['accepted_quality_frame']=quality['frame']
            diagnostic['output_advances']=advance
            return frame, quality.copy()
    raise TimeoutError("no fresh usable still image after settling")


def run_stop_capture_loop(core, sender, controls, pulse_us, usable_frame,
                          move_duration=0.12, stop_duration=2.0,
                          settle_duration=1.0, positions=96, move_durations=None,
                          still_acquirer=None):
    if not 1 <= positions <= 360:
        raise ValueError("positions must be 1..360")
    if not 0.5 <= settle_duration < stop_duration <= 10:
        raise ValueError("need 0.5 <= settle < stop <= 10 seconds")
    if move_durations is not None:
        if len(move_durations) != positions or any(not .02 <= d <= 1.0 for d in move_durations):
            raise ValueError('one bounded duration is required for each position')
        move_durations = [int(d / .020 + .5) * .020 for d in move_durations]
        move_duration = move_durations[0]
    previous, move_s = core.arm_stop_capture(pulse_us, move_duration)
    planned_duration = sum(move_durations or [move_s] * positions) + positions * stop_duration
    plan = {"capture_mode": "stop-and-shoot", "positions": positions,
            "move_duration_s": move_s, "minimum_stop_s": stop_duration,
            "settle_before_sample_s": settle_duration,
            "motion_command_us": pulse_us, "stop_command_us": 1500,
            "nominal_angle_step_deg": 360.0/positions,
            "angle_note": "nominal step is target spacing 360/positions, not measured rotation",
            "angle_calibrated": False, "full_turn_verified": False,
            "timing_note": "stop can extend during frame read/transfer; no angle feedback"}
    if move_durations is not None:
        plan['move_durations_s'] = move_durations
    print("READY_STOP_CAPTURE " + json.dumps(plan, sort_keys=True), flush=True)
    sequence = 0
    active_round = None
    captured = 0
    try:
        while True:
            command = controls.poll()
            if command is not None:
                try:
                    core.set_image_mode(*command)
                except RuntimeError as exc:
                    print("IMAGE_MODE_REJECTED %s" % exc, flush=True)
            trigger, status, trigger_frame = core.button_capture_state()
            if trigger == previous:
                time.sleep(0.005)
                continue
            if trigger != (previous + 1) & 0xFFFFFFFF:
                raise RuntimeError("unexpected trigger before capture round")
            # Ignore all further physical KEY0 presses until the round ends.
            # Do not disarm here: the first configured hardware timer is running.
            core.lock_stop_capture_round()
            active_round = trigger
            captured = 0
            sender.capture_event(True, active_round, planned_duration, trigger_frame)
            sender.send_capture_metadata({"kind": "plan", "trigger": active_round,
                                          "plan": plan})
            started = time.monotonic()
            for position in range(positions):
                if position:
                    if move_durations is not None:
                        move_s = core.set_short_rotation_duration(move_durations[position])
                    previous_move = core.trigger_short_rotation()
                    trigger = _wait_new_trigger(core, previous_move)
                stopped_at = _wait_short_stop(core, trigger, timeout=max(1.0, move_s + .5))
                _wait_until(stopped_at + settle_duration)
                def acquire_front():
                    return _fresh_still_frame(core, usable_frame,
                        timeout=20.0 if core.width >= 2592 else 3.0)
                frame, quality = (acquire_front() if still_acquirer is None else
                                  still_acquirer(position, acquire_front))
                telemetry = core.telemetry()
                if telemetry["servo_pulse_us"] != 1500:
                    raise RuntimeError("neutral PWM lost before still transmission")
                sample = {"kind": "sample", "trigger": active_round,
                          "sequence": sequence, "position_index": position,
                          "positions": positions, "hardware_trigger": trigger,
                          "move_duration_s": move_s,
                          "stop_elapsed_at_copy_s": time.monotonic()-stopped_at,
                          "sample_frame": quality["frame"],
                          "angle_estimate_deg": None, "angle_calibrated": False,
                          "capture_mode": "stop-and-shoot"}
                sender.send_capture_metadata(sample)
                sender.send_quality(sequence, quality)
                sender.send(frame, width=frame.shape[1], height=frame.shape[0],
                            sequence=sequence, telemetry=telemetry,
                            compress=frame.shape[1] > 800)
                captured += 1
                sequence = (sequence+1) & 0xFFFFFFFF
                print("STILL_SAMPLE " + json.dumps(sample, sort_keys=True), flush=True)
                # Extend neutral as necessary. Never overlap move and transfer.
                _wait_until(stopped_at + stop_duration)
                _require_stopped(core)
            time.sleep(0.1)
            sender.capture_event(False, active_round, planned_duration, captured)
            print("CAPTURE_DONE mode=stop-and-shoot trigger=%d sent=%d elapsed_s=%.3f" %
                  (active_round, captured, time.monotonic()-started), flush=True)
            active_round = None
            previous = trigger
            if move_durations is not None:
                # Each future KEY0 round must start with the first timer again.
                move_s = core.set_short_rotation_duration(move_durations[0])
            core.rearm_stop_capture()
    finally:
        core.stop_servo_rotation()
        if active_round is not None:
            sender.capture_abort(active_round, planned_duration, captured)
