"""ffmpeg wrapper tests.

The subprocess command construction is tested without touching the network;
one real smoke test generates a synthetic clip with ffmpeg lavfi (skip when
ffmpeg is absent, e.g. a minimal CI image).
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from mediavault import media
from mediavault.media import MediaToolError, extract_audio, extract_keyframes, probe_media

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def run(coro):
    return asyncio.run(coro)


class FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


def test_require_missing_binary(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(MediaToolError, match="not found on PATH"):
        media._require("ffmpeg")


def test_run_nonzero_exit_raises(monkeypatch):
    recorded = {}

    async def fake_exec(*cmd, stdout, stderr):
        recorded["cmd"] = cmd
        return FakeProcess(returncode=1, stderr=b"boom detail")

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(MediaToolError, match="exited 1"):
        run(media._run(["ffmpeg", "-i", "x"]))
    assert recorded["cmd"][0] == "ffmpeg"


def test_probe_parses_streams_and_duration(monkeypatch):
    payload = (
        '{"streams": [{"codec_type": "video"}, {"codec_type": "audio"}],'
        ' "format": {"duration": "12.5"}}'
    )

    async def fake_run(cmd):
        return payload

    monkeypatch.setattr(media, "_run", fake_run)
    probe = run(probe_media("clip.mp4"))
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.duration_ms == 12500


def test_probe_handles_missing_duration(monkeypatch):
    async def fake_run(cmd):
        return '{"streams": [{"codec_type": "video"}], "format": {}}'

    monkeypatch.setattr(media, "_run", fake_run)
    probe = run(probe_media("clip.mp4"))
    assert probe.duration_ms is None
    assert probe.has_audio is False


def test_extract_audio_builds_16k_mono_command(monkeypatch, tmp_path):
    seen = {}

    async def fake_run(cmd):
        seen["cmd"] = cmd
        # emulate output file
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF....")
        return ""

    monkeypatch.setattr(media, "_run", fake_run)
    out = run(extract_audio("clip.mp4", tmp_path / "audio.wav"))
    cmd = seen["cmd"]
    assert cmd[0].endswith("ffmpeg")
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert out == tmp_path / "audio.wav"


def test_extract_audio_empty_output_raises(monkeypatch, tmp_path):
    async def fake_run(cmd):
        return ""

    monkeypatch.setattr(media, "_run", fake_run)
    with pytest.raises(MediaToolError, match="no output"):
        run(extract_audio("clip.mp4", tmp_path / "audio.wav"))


def test_probe_tolerates_bad_duration(monkeypatch):
    async def fake_run(cmd):
        return '{"streams": [], "format": {"duration": "N/A"}}'

    monkeypatch.setattr(media, "_run", fake_run)
    probe = run(probe_media("clip.mp4"))
    assert probe.duration_ms is None


def test_extract_keyframes_unknown_duration_uses_default_fps(monkeypatch, tmp_path):
    seen = {}

    async def fake_probe(path):
        return media.MediaProbe(duration_ms=None, has_audio=False, has_video=True)

    async def fake_run(cmd):
        seen["cmd"] = cmd
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "_run", fake_run)
    frames = run(extract_keyframes("clip.mp4", tmp_path / "frames", max_frames=4, width=480))
    assert len(frames) == 1
    assert "fps=1.0000" in seen["cmd"][seen["cmd"].index("-vf") + 1]


def test_extract_keyframes_no_video_stream_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(
        media,
        "probe_media",
        lambda path: _coro(media.MediaProbe(duration_ms=5000, has_audio=True, has_video=False)),
    )
    assert run(extract_keyframes("a.mp3", tmp_path / "frames", max_frames=8, width=768)) == []


def test_extract_keyframes_caps_and_spreads(monkeypatch, tmp_path):
    seen = {}

    async def fake_probe(path):
        return media.MediaProbe(duration_ms=20000, has_audio=True, has_video=True)

    async def fake_run(cmd):
        seen["cmd"] = cmd
        pattern = Path(cmd[-1])
        out_dir = pattern.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        # emulate three frames
        for i in range(1, 4):
            (out_dir / f"frame_{i:03d}.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "_run", fake_run)
    frames = run(extract_keyframes("clip.mp4", tmp_path / "frames", max_frames=50, width=640))
    assert len(frames) == 3
    cmd = seen["cmd"]
    # requested 50 but capped to 32
    assert cmd[cmd.index("-frames:v") + 1] == "32"
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=640:-2" in vf
    assert "fps=" in vf


async def _coro(value):
    return value


@requires_ffmpeg
def test_real_ffmpeg_smoke(tmp_path):
    clip = tmp_path / "synthetic.mp4"
    # 2 seconds: test pattern video + sine tone, fully offline
    run(_make_clip(clip))
    probe = run(probe_media(clip))
    assert probe.has_video and probe.has_audio
    assert probe.duration_ms and 1500 <= probe.duration_ms <= 3000

    wav = run(extract_audio(clip, tmp_path / "audio.wav"))
    assert wav.stat().st_size > 1000

    frames = run(extract_keyframes(clip, tmp_path / "frames", max_frames=4, width=320))
    assert 1 <= len(frames) <= 4
    assert all(p.suffix == ".jpg" and p.stat().st_size > 0 for p in frames)


async def _make_clip(path: Path):
    cmd = [
        shutil.which("ffmpeg"), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", str(path),
    ]
    proc = await media.asyncio.create_subprocess_exec(
        *cmd, stdout=media.asyncio.subprocess.PIPE, stderr=media.asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# M3: scene-cut detection, timestamp planning and scene-aware extraction
# --------------------------------------------------------------------------- #
def test_plan_timestamps_merges_anchors_and_cuts_within_budget():
    # 10s video, budget 5 -> anchors at 0,2,4,6,8; cuts at 3 and 7 added.
    ts = media.plan_keyframe_timestamps(10.0, 5, [3.0, 7.0])
    assert ts[0] == 0.0 and ts[-1] == 8.0
    assert 3.0 in ts  # a detected cut survives the budget downsampling
    assert len(ts) <= 5


def test_plan_timestamps_caps_overflow_and_keeps_ends():
    ts = media.plan_keyframe_timestamps(20.0, 4, [1.0, 2.0, 5.0, 9.0, 13.0, 17.0])
    assert len(ts) <= 4
    assert ts[0] == 0.0  # first anchor always kept


def test_plan_timestamps_scene_only_empty_without_cuts():
    assert media.plan_keyframe_timestamps(10.0, 5, [], include_anchors=False) == []


def test_plan_timestamps_drops_out_of_range_and_duplicates():
    ts = media.plan_keyframe_timestamps(10.0, 8, [-3.0, 0.0, 10.0, 10.0001])
    assert all(0.0 <= t <= 10.0 for t in ts)
    assert len(ts) == len(set(ts))


def test_plan_timestamps_budget_one():
    assert media.plan_keyframe_timestamps(10.0, 1, [5.0]) == [0.0]


def test_plan_timestamps_unknown_duration_empty():
    assert media.plan_keyframe_timestamps(0.0, 5, [1.0]) == []


def test_detect_scene_cuts_parses_showinfo_and_collapses_close(monkeypatch):
    stderr = (
        "[Parsed_showinfo] n:0 pts:1280 pts_time:1.28 "
        "[Parsed_showinfo] n:1 pts:1300 pts_time:1.40 "
        "[Parsed_showinfo] n:2 pts:8000 pts_time:8.0 "
    )

    async def fake_capture(cmd):
        return "", stderr

    monkeypatch.setattr(media, "_run_capture", fake_capture)
    cuts = run(media.detect_scene_cuts("clip.mp4", threshold=0.4))
    # 1.28 and 1.40 are within the 0.5s minimum gap -> collapsed
    assert cuts == [1.28, 8.0]


def test_extract_at_timestamps_builds_select_filter(monkeypatch, tmp_path):
    seen = {}

    async def fake_run(cmd):
        seen["cmd"] = cmd
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 3):
            (out_dir / f"frame_{i:03d}.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "_run", fake_run)
    frames = run(media._extract_at_timestamps("clip.mp4", tmp_path, [0.0, 5.0], width=480))
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert "between(t," in vf and "scale=480:-2" in vf
    assert len(frames) == 2


def test_detailed_no_video_returns_empty(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=4000, has_audio=False, has_video=False)

    monkeypatch.setattr(media, "probe_media", fake_probe)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="hybrid"
    ))
    assert result.frames == [] and result.scene_cuts == 0


def test_detailed_interval_mode_reports_zero_cuts(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=4000, has_audio=True, has_video=True)

    async def fake_run(cmd):
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "_run", fake_run)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="interval"
    ))
    assert result.scene_cuts == 0 and len(result.frames) == 1


def test_detailed_hybrid_uses_scene_timestamps(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=10000, has_audio=True, has_video=True)

    async def fake_cuts(path, *, threshold):
        return [2.0, 7.0]

    async def fake_grab(video, out_dir, timestamps, *, width):
        out_dir.mkdir(parents=True, exist_ok=True)
        made = []
        for i, _ts in enumerate(timestamps, start=1):
            p = out_dir / f"frame_{i:03d}.jpg"
            p.write_bytes(b"jpeg")
            made.append(p)
        return made

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(media, "_extract_at_timestamps", fake_grab)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=6, width=320, mode="hybrid"
    ))
    assert result.scene_cuts == 2
    assert len(result.frames) >= 1


def test_detailed_hybrid_detect_error_falls_back_to_interval(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=10000, has_audio=True, has_video=True)

    async def boom(path, *, threshold):
        raise media.MediaToolError("ffmpeg exited 1")

    async def fake_run(cmd):
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "detect_scene_cuts", boom)
    monkeypatch.setattr(media, "_run", fake_run)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="hybrid"
    ))
    assert result.scene_cuts == 0 and len(result.frames) == 1


def test_detailed_unknown_duration_uses_interval(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=None, has_audio=True, has_video=True)

    async def fake_run(cmd):
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "_run", fake_run)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="scene"
    ))
    assert result.scene_cuts == 0 and len(result.frames) == 1


def test_extract_keyframes_wrapper_returns_only_paths(monkeypatch, tmp_path):
    async def fake_detailed(*_args, **_kwargs):
        return media.KeyframeResult([Path("a.jpg"), Path("b.jpg")], 3)

    monkeypatch.setattr(media, "extract_keyframes_detailed", fake_detailed)
    paths = run(media.extract_keyframes(
        "clip.mp4", tmp_path, max_frames=2, width=320, mode="hybrid"
    ))
    assert paths == [Path("a.jpg"), Path("b.jpg")]


@requires_ffmpeg
def test_real_ffmpeg_scene_detection(tmp_path):
    clip = tmp_path / "scenes.mp4"
    # Two back-to-back solid-color blocks create at least one hard cut.
    cmd = [
        shutil.which("ffmpeg"), "-y",
        "-f", "lavfi", "-i", "color=c=red:duration=1:size=320x240:rate=15",
        "-f", "lavfi", "-i", "color=c=blue:duration=1:size=320x240:rate=15",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
        str(clip),
    ]
    run(_run_simple(cmd))
    cuts = run(media.detect_scene_cuts(clip, threshold=0.3))
    assert isinstance(cuts, list)
    detailed = run(media.extract_keyframes_detailed(
        clip, tmp_path / "hybrid", max_frames=4, width=160, mode="hybrid"
    ))
    assert detailed.frames and all(p.stat().st_size > 0 for p in detailed.frames)


async def _run_simple(cmd):
    proc = await media.asyncio.create_subprocess_exec(
        *cmd, stdout=media.asyncio.subprocess.PIPE, stderr=media.asyncio.subprocess.PIPE
    )
    _out, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")

def test_detailed_scene_mode_no_cuts_falls_back_to_interval(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=10000, has_audio=True, has_video=True)

    async def no_cuts(path, *, threshold):
        return []

    async def fake_run(cmd):
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "detect_scene_cuts", no_cuts)
    monkeypatch.setattr(media, "_run", fake_run)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="scene"
    ))
    # no cuts -> empty scene-only plan -> interval fallback, zero cut count
    assert result.scene_cuts == 0 and len(result.frames) == 1


def test_detailed_empty_grab_falls_back_to_interval(monkeypatch, tmp_path):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms=10000, has_audio=True, has_video=True)

    async def fake_cuts(path, *, threshold):
        return [2.0, 6.0]

    async def empty_grab(video, out_dir, timestamps, *, width):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return []  # select filter produced nothing usable

    async def fake_run(cmd):
        out_dir = Path(cmd[-1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_001.jpg").write_bytes(b"jpeg")
        return ""

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(media, "_extract_at_timestamps", empty_grab)
    monkeypatch.setattr(media, "_run", fake_run)
    result = run(media.extract_keyframes_detailed(
        "clip.mp4", tmp_path / "f", max_frames=4, width=320, mode="hybrid"
    ))
    assert result.scene_cuts == 2 and len(result.frames) == 1