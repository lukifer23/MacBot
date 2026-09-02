"""Exercise the actual resident synthesizer, bounded cache and cancellation."""

import threading

import numpy as np
import pytest

from macbot.config import Settings
from macbot.speech import Synthesizer, split_speech

pytestmark = pytest.mark.models


@pytest.mark.parametrize("voice,rate", [("qwen-aiden-1.7b", 24000)])
def test_real_synthesis_cache_and_cancellation(voice, rate):
    settings = Settings()
    settings.models.tts_voice = voice
    synth = Synthesizer(settings)
    cancel = threading.Event()
    first = list(synth.chunks("This is a real synthesis test.", cancel))
    assert first and all(actual_rate == rate for _, actual_rate in first)
    assert all(samples.size > 0 and np.isfinite(samples).all() for samples, _ in first)
    second = list(synth.chunks("This is a real synthesis test.", cancel))
    assert len(first) == len(second)
    for (a, rate), (b, cached_rate) in zip(first, second, strict=True):
        assert rate == cached_rate and np.array_equal(a, b)
    assert 0 < synth.cache_bytes <= 16 * 1024 * 1024
    cancel.set()
    assert list(synth.chunks("This is a real synthesis test.", cancel)) == []
    assert list(synth.chunks("A different uncached sentence.", cancel)) == []


def test_incremental_speech_preserves_words_decimals_and_abbreviations():
    text = (
        "Dr. Smith measured 3.5 seconds. "
        + ("Longer conversational wording " * 15)
        + "finishes here."
    )
    pending = ""
    phrases = []
    # Single-character delivery deliberately bisects every word and number.
    for character in text:
        emitted, pending = split_speech(pending + character)
        phrases.extend(emitted)
    emitted, pending = split_speech(pending, final=True)
    phrases.extend(emitted)
    assert not pending
    assert phrases[0] == "Dr. Smith measured 3.5 seconds."
    assert " ".join(phrases).split() == text.split()
    assert all(len(phrase) <= 180 for phrase in phrases)


def test_speech_retains_partial_final_word_until_flush():
    assert split_speech("A conversational fragm") == ([], "A conversational fragm")
    assert split_speech("A conversational fragment.", final=True) == (
        ["A conversational fragment."],
        "",
    )


def test_product_configuration_has_no_unimplemented_speech_speed():
    settings = Settings()
    assert "tts_speed" not in type(settings.models).model_fields
