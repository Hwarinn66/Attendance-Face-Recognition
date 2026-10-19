from __future__ import annotations

import base64
import os
import uuid

import numpy as np
import pytest

from app.biometrics.crypto import TemplateCipher, TemplateDecryptionError
from app.biometrics.decision import BandPolicy, Category, Decision, Reason, match_gate, pad_gate
from app.biometrics.models import l2_normalize
from app.core.config import ConfigurationError, parse_keyring

PAD = BandPolicy(threshold=0.8, margin=0.1)
MATCH = BandPolicy(threshold=0.363, margin=0.05)


class TestPadGate:
    def test_pass(self) -> None:
        assert pad_gate([0.95, 0.9, 0.85], PAD) is None

    def test_one_weak_frame_is_uncertain(self) -> None:
        assert pad_gate([0.99, 0.99, 0.65], PAD) == Reason.PAD_UNCERTAIN

    def test_low_mean_is_spoof(self) -> None:
        assert pad_gate([0.3, 0.5, 0.6], PAD) == Reason.PAD_SPOOF_SUSPECTED

    def test_band(self) -> None:
        assert pad_gate([0.75, 0.75], PAD) == Reason.PAD_UNCERTAIN

    def test_empty_never_passes(self) -> None:
        assert pad_gate([], PAD) == Reason.PAD_UNCERTAIN


class TestMatchGate:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.9, None),
            (0.413, None),
            (0.4129, Reason.MATCH_UNCERTAIN),
            (0.363, Reason.MATCH_UNCERTAIN),
            (0.3629, Reason.NO_MATCH),
            (-0.2, Reason.NO_MATCH),
        ],
    )
    def test_bands(self, score: float, expected: Reason | None) -> None:
        assert match_gate(score, MATCH) == expected


def test_public_decision_hides_scores() -> None:
    d = Decision(accepted=False, reasons=[Reason.PAD_SPOOF_SUSPECTED], pad_score=0.12, match_score=0.9)
    pub = d.public()
    assert pub["category"] == Category.LIVENESS
    assert "0.12" not in str(pub) and "pad" not in str(pub).lower()


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class TestTemplateCipher:
    def setup_method(self) -> None:
        self.cipher = TemplateCipher(parse_keyring(f"k1:{_key()}"))
        self.emb = l2_normalize(np.random.default_rng(1).normal(size=128))
        self.tid, self.uid = uuid.uuid4(), uuid.uuid4()

    def test_round_trip(self) -> None:
        sealed = self.cipher.seal(self.emb, template_id=self.tid, user_id=self.uid, model_name="m")
        assert self.emb.tobytes() not in sealed.ciphertext
        out = self.cipher.open(sealed, template_id=self.tid, user_id=self.uid, model_name="m", dim=128)
        np.testing.assert_array_equal(out, self.emb)

    def test_ciphertext_bound_to_user(self) -> None:
        sealed = self.cipher.seal(self.emb, template_id=self.tid, user_id=self.uid, model_name="m")
        with pytest.raises(TemplateDecryptionError):
            self.cipher.open(sealed, template_id=self.tid, user_id=uuid.uuid4(), model_name="m", dim=128)

    def test_ciphertext_bound_to_model(self) -> None:
        sealed = self.cipher.seal(self.emb, template_id=self.tid, user_id=self.uid, model_name="m")
        with pytest.raises(TemplateDecryptionError):
            self.cipher.open(sealed, template_id=self.tid, user_id=self.uid, model_name="other", dim=128)

    def test_key_rotation_reads_old_keys(self) -> None:
        old_b64 = _key()
        old = TemplateCipher(parse_keyring(f"old:{old_b64}"))
        sealed = old.seal(self.emb, template_id=self.tid, user_id=self.uid, model_name="m")
        rotated = TemplateCipher(parse_keyring(f"new:{_key()},old:{old_b64}"))
        assert rotated.active_key_id == "new"
        rotated.open(sealed, template_id=self.tid, user_id=self.uid, model_name="m", dim=128)

    def test_unknown_key(self) -> None:
        sealed = self.cipher.seal(self.emb, template_id=self.tid, user_id=self.uid, model_name="m")
        other = TemplateCipher(parse_keyring(f"k2:{_key()}"))
        with pytest.raises(TemplateDecryptionError):
            other.open(sealed, template_id=self.tid, user_id=self.uid, model_name="m", dim=128)


class TestKeyring:
    def test_rejects_short_key(self) -> None:
        with pytest.raises(ConfigurationError):
            parse_keyring("k:" + base64.b64encode(b"short").decode())

    def test_rejects_bad_format(self) -> None:
        with pytest.raises(ConfigurationError):
            parse_keyring("no-colon")

    def test_rejects_duplicate(self) -> None:
        k = _key()
        with pytest.raises(ConfigurationError):
            parse_keyring(f"a:{k},a:{k}")
