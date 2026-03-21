from pathlib import Path

import numpy as np

from cleaningcar.events import EventManager
from cleaningcar import video_io
from zone_manager import ZoneManager


A_ZONE = [(0, 0), (0, 20), (20, 20), (20, 0)]
B_ZONE = [(5, 5), (5, 15), (15, 15), (15, 5)]
FLOW = ((0, 0), (0, 20))
BOX = (0, 0, 10, 10)
WATER_BOX = (30, 30, 35, 35)


class DummyWriter:
    def __init__(self, path, release_result=True):
        self.path = str(path)
        self.release_result = release_result
        self.release_calls = 0

    def release(self):
        self.release_calls += 1
        return self.release_result


class DummyEventManager:
    def __init__(self):
        self.calls = []

    def emit_event(self, *args):
        self.calls.append(args)


def build_manager(tmp_path, fps=10.0, timeout_frames=2):
    config = {
        "camera_id": "CAM-TEST",
        "system": {"device_id": "DEV-TEST"},
        "event_capture_dir": str(tmp_path / "captures"),
        "event_output_dir": str(tmp_path / "events"),
        "track_timeout_frames": timeout_frames,
        "logic": {
            "min_track_frames_for_type1": 0,
            "min_zone_a_dwell_frames_for_type5": 0,
            "min_zone_b_dwell_frames_for_type4": 0,
            "zone_b_anchor_min_frames": 0,
            "require_vehicle_type_for_events": False,
            "disable_plate_only_events": False,
            "single_lifecycle_events": True,
        },
    }
    zone_mgr = ZoneManager(A_ZONE, B_ZONE, FLOW, entry_hysteresis=1, exit_hysteresis=1)
    manager = EventManager(config, fps, (40, 40), zone_mgr)
    manager.min_type1_track_frames = 0
    manager.min_type5_zone_a_dwell = 0
    manager.min_type4_zone_b_dwell = 0
    manager.zone_b_anchor_min_frames = 0
    manager.require_vehicle_type_for_events = False
    manager.disable_plate_only_events = False
    manager._save_event_capture = lambda *args, **kwargs: ""
    manager.frame_timestamp = lambda frame_idx: f"2026-03-21 00:00:{int(frame_idx):02d}"
    return manager


def update_vehicle(manager, track_id, frame_idx, anchor_point, *, water_boxes=None):
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    manager.update_track(
        track_id,
        None,
        BOX,
        "",
        frame_idx,
        frame,
        water_boxes or [],
        bool(water_boxes),
        is_plate=False,
        vehicle_label="car",
        vehicle_conf=0.95,
        plate_conf=None,
        confirmed=True,
        anchor_point=anchor_point,
    )


def test_type3_first_water_frame_after_type2_qualification_triggers_immediately(tmp_path):
    manager = build_manager(tmp_path, fps=10.0)

    update_vehicle(manager, 1, 0, (2, 2))
    update_vehicle(manager, 1, 1, (8, 8))
    update_vehicle(manager, 1, 2, (8, 8), water_boxes=[WATER_BOX])

    state = manager.tracks[1]
    assert 2 in state["events"]
    assert 3 in state["events"]
    assert state["wash_start_time"] == "2026-03-21 00:00:02"


def test_type5_requires_type2_qualification_for_exit_a(tmp_path):
    manager = build_manager(tmp_path)

    update_vehicle(manager, 1, 0, (2, 2))
    update_vehicle(manager, 1, 1, (30, 30))

    state = manager.tracks[1]
    assert 1 in state["events"]
    assert 2 not in state["events"]
    assert 5 not in state["events"]


def test_type5_requires_type2_qualification_on_timeout_backfill(tmp_path):
    manager = build_manager(tmp_path, timeout_frames=2)

    update_vehicle(manager, 1, 0, (2, 2))
    update_vehicle(manager, 1, 1, (3, 3))
    state = manager.tracks[1]
    manager.flush_inactive(set(), 4)

    assert 5 not in state["events"]
    assert state["record_stop_frame"] is not None


def test_type5_sets_per_id_video_tail_to_five_seconds_for_valid_flow(tmp_path):
    manager = build_manager(tmp_path, fps=10.0)

    update_vehicle(manager, 1, 0, (2, 2))
    update_vehicle(manager, 1, 1, (8, 8))
    update_vehicle(manager, 1, 2, (30, 30))

    state = manager.tracks[1]
    assert 2 in state["events"]
    assert 5 in state["events"]
    assert state["record_stop_frame"] == 52


def test_finalize_per_id_recording_deletes_invalid_video_without_type6(tmp_path):
    assert hasattr(video_io, "finalize_per_id_recording")
    finalize = video_io.finalize_per_id_recording
    video_path = tmp_path / "invalid.mp4"
    video_path.write_bytes(b"video-data")
    writer = DummyWriter(video_path)
    event_manager = DummyEventManager()
    track_state = {
        "type2_qualified": False,
        "record_stop_frame": 12,
        "last_frame_idx": 12,
        "last_frame": None,
    }

    kept = finalize(writer, 7, track_state, event_manager)

    assert kept is False
    assert not video_path.exists()
    assert event_manager.calls == []


def test_finalize_per_id_recording_keeps_valid_video_and_emits_type6(tmp_path):
    assert hasattr(video_io, "finalize_per_id_recording")
    finalize = video_io.finalize_per_id_recording
    video_path = tmp_path / "valid.mp4"
    video_path.write_bytes(b"video-data")
    writer = DummyWriter(video_path)
    event_manager = DummyEventManager()
    track_state = {
        "type2_qualified": True,
        "record_stop_frame": 18,
        "last_frame_idx": 17,
        "last_frame": None,
    }

    kept = finalize(writer, 8, track_state, event_manager)

    assert kept is True
    assert video_path.exists()
    assert len(event_manager.calls) == 1
    assert event_manager.calls[0][0] == 8
    assert event_manager.calls[0][1] == 6
    assert event_manager.calls[0][2] == 18
