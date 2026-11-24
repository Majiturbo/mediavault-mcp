"""Speaker diarization tests: the dependency-free pause-gap heuristic and builder."""

import pytest

from mediavault.asr import TranscriptSegment
from mediavault.config import Config
from mediavault.diarize import (
    DiarizationError,
    PauseGapDiarizer,
    build_diarizer,
)


def cfg(tmp_path, **env):
    payload = {"MEDIAVAULT_HOME": str(tmp_path / "vault")}
    payload.update(env)
    return Config.load(payload)


def test_short_pause_keeps_same_speaker():
    d = PauseGapDiarizer(gap_ms=700)
    segs = [TranscriptSegment(0, 500, "hi"), TranscriptSegment(600, 900, "there")]
    turns = d.diarize(segs)
    assert [t.speaker for t in turns] == ["SPEAKER_A", "SPEAKER_A"]
    assert turns[0].start_ms == 0 and turns[1].end_ms == 900


def test_long_pause_flips_speaker_and_flips_back():
    d = PauseGapDiarizer(gap_ms=700)
    segs = [
        TranscriptSegment(0, 500, "a1"),
        TranscriptSegment(1500, 2000, "b1"),   # 1000ms gap -> flip
        TranscriptSegment(2100, 2400, "a?"),  # short gap -> stays B
        TranscriptSegment(4000, 4400, "a2"),  # long gap -> flip back to A
    ]
    assert [t.speaker for t in d.diarize(segs)] == [
        "SPEAKER_A", "SPEAKER_B", "SPEAKER_B", "SPEAKER_A",
    ]


def test_gap_boundary_is_inclusive():
    d = PauseGapDiarizer(gap_ms=700)
    segs = [TranscriptSegment(0, 300, "x"), TranscriptSegment(1000, 1200, "y")]
    # gap is exactly 700ms (1000 - 300): threshold met -> flip
    assert [t.speaker for t in d.diarize(segs)] == ["SPEAKER_A", "SPEAKER_B"]


def test_missing_timestamps_never_flip():
    d = PauseGapDiarizer(gap_ms=10)
    segs = [TranscriptSegment(None, None, "x"), TranscriptSegment(None, None, "y")]
    assert [t.speaker for t in d.diarize(segs)] == ["SPEAKER_A", "SPEAKER_A"]


def test_empty_and_single_segment():
    d = PauseGapDiarizer()
    assert d.diarize([]) == []
    turns = d.diarize([TranscriptSegment(0, 100, "solo")])
    assert len(turns) == 1 and turns[0].speaker == "SPEAKER_A"


def test_build_diarizer_off_returns_none(tmp_path):
    assert build_diarizer(cfg(tmp_path)) is None


def test_build_diarizer_heuristic_uses_configured_gap(tmp_path):
    diarizer = build_diarizer(
        cfg(tmp_path, MEDIAVAULT_DIARIZATION="heuristic", MEDIAVAULT_DIARIZE_GAP_MS="1500")
    )
    assert isinstance(diarizer, PauseGapDiarizer)
    assert diarizer.gap_ms == 1500
    assert diarizer.name == "pause-gap-heuristic"


def test_build_diarizer_unknown_mode_raises(tmp_path, monkeypatch):
    # Config.load() rejects unknown choices, so force the value to hit the guard.
    config = cfg(tmp_path, MEDIAVAULT_DIARIZATION="heuristic")
    object.__setattr__(config, "diarization", "voiceprint")
    with pytest.raises(DiarizationError, match="unknown MEDIAVAULT_DIARIZATION"):
        build_diarizer(config)
