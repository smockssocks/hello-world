import numpy as np

from ds5forge.haptics import synth


def test_effects_render_at_any_rate():
    for sr in (3000, 48000):
        for make in (synth.click, synth.thump, synth.impact):
            src = make()
            out = src.render(sr // 100, sr)
            assert out.shape == (sr // 100, 2)
            assert np.isfinite(out).all()


def test_oneshot_finishes():
    src = synth.Tone(100, 1.0, 0.05)
    for _ in range(10):
        src.render(30, 3000)
    assert src.done


def test_mixer_soft_clips_and_cleans_up():
    m = synth.HapticMixer()
    for _ in range(10):
        m.play(synth.Tone(80, 1.0, 0.02))
    out = m.render(480, 48000)
    assert np.abs(out).max() <= 1.0001
    for _ in range(5):
        m.render(480, 48000)
    assert m._active_sources() == []


def test_continuous_channel_and_rumble_levels():
    m = synth.HapticMixer()
    m.channel("engine").set(freq=50, amp=0.8)
    m.render(32, 3000)
    low, high = m.rumble_levels()
    assert low > 0.5 and high == 0
    m.silence("engine")
    assert m.rumble_levels() == (0, 0)


def test_pan_is_respected():
    src = synth.Tone(200, 1.0, 0.1, pan=-1, attack=0)
    out = src.render(300, 3000)
    assert np.abs(out[:, 1]).max() == 0 and np.abs(out[:, 0]).max() > 0.5


def test_moving_average_streaming_matches_batch():
    x = np.random.default_rng(1).normal(size=1000)
    full = synth.MovingAverage(8).process(x)
    ma = synth.MovingAverage(8)
    parts = np.concatenate([ma.process(x[:300]), ma.process(x[300:])])
    assert np.allclose(full, parts)
