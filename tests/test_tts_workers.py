"""Exercise the actual resident synthesizer, bounded cache and cancellation."""

import threading

import numpy as np
import pytest

from macbot.config import load
from macbot.speech import Synthesizer

pytestmark = pytest.mark.models


def test_real_synthesis_cache_and_cancellation():
    settings = load()
    synth = Synthesizer(settings)
    cancel = threading.Event()
    first = list(synth.chunks("This is a real synthesis test.", cancel))
    assert first and all(rate == 22050 for _, rate in first)
    assert all(samples.size > 0 and np.isfinite(samples).all() for samples, _ in first)
    second = list(synth.chunks("This is a real synthesis test.", cancel))
    assert len(first) == len(second)
    for (a, rate), (b, cached_rate) in zip(first, second, strict=True):
        assert rate == cached_rate and np.array_equal(a, b)
    assert 0 < synth.cache_bytes <= 16 * 1024 * 1024
    cancel.set()
    assert list(synth.chunks("This is a real synthesis test.", cancel)) == []
    assert list(synth.chunks("A different uncached sentence.", cancel)) == []
