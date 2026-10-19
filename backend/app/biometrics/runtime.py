"""Process-wide biometric runtime: provider, inference pool, cipher and the derived pipeline config."""

from __future__ import annotations

from dataclasses import dataclass

from app.biometrics.challenge import ChallengeConfig
from app.biometrics.crypto import TemplateCipher
from app.biometrics.decision import BandPolicy
from app.biometrics.pipeline import InferenceExecutor, PipelineConfig
from app.biometrics.provider import VisionProvider, get_provider
from app.biometrics.quality import QualityThresholds
from app.core.config import Settings


@dataclass(slots=True)
class BiometricRuntime:
    settings: Settings
    provider: VisionProvider
    executor: InferenceExecutor
    cipher: TemplateCipher

    @property
    def match_policy(self) -> BandPolicy:
        assert self.settings.match_threshold is not None
        return BandPolicy(self.settings.match_threshold, self.settings.match_uncertainty_margin)

    @property
    def pad_required(self) -> bool:
        return self.settings.pad_mode == "required"

    def pipeline_config(self) -> PipelineConfig:
        s = self.settings
        assert s.match_threshold is not None
        return PipelineConfig(
            quality=QualityThresholds(
                min_detection_score=s.quality_min_detection_score,
                min_face_width_ratio=s.quality_min_face_width_ratio,
                max_face_width_ratio=s.quality_max_face_width_ratio,
                min_brightness=s.quality_min_brightness,
                max_brightness=s.quality_max_brightness,
                min_sharpness=s.quality_min_sharpness,
                max_center_offset=s.quality_max_center_offset,
                secondary_face_score=s.detector_score_threshold,
            ),
            challenge=ChallengeConfig(
                yaw_threshold=s.challenge_yaw_threshold,
                pitch_threshold=s.challenge_pitch_threshold,
                center_yaw_tolerance=s.challenge_center_yaw_tolerance,
                center_pitch_tolerance=s.challenge_center_pitch_tolerance,
                min_pose_frames=s.challenge_min_pose_frames,
                max_missing_ratio=s.challenge_max_missing_ratio,
                max_consecutive_missing=s.challenge_max_consecutive_missing,
            ),
            max_center_jump=s.temporal_max_center_jump,
            max_scale_jump=s.temporal_max_scale_jump,
            identity_threshold=s.match_threshold,
            pad_policy=BandPolicy(s.pad_threshold, s.pad_uncertainty_margin),
            pad_required=self.pad_required,
            pad_max_frames=s.pad_max_frames,
            min_frontal_frames=s.enrollment_min_frontal_frames,
        )

    def shutdown(self) -> None:
        self.executor.shutdown()


def build_runtime(settings: Settings) -> BiometricRuntime:
    return BiometricRuntime(
        settings=settings,
        provider=get_provider(settings),
        executor=InferenceExecutor(settings.inference_workers),
        cipher=TemplateCipher(settings.keyring),
    )
