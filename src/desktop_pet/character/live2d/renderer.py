from abc import ABC, abstractmethod


class Live2DRenderer(ABC):
    """Contract for an official Cubism SDK renderer.

    load_model must finish SDK validation and texture upload before signaling ready.
    Parameter ranges must come from the loaded moc3, not assumed preset ranges.
    The caller retains the previous model until candidate initialization succeeds.
    """

    @abstractmethod
    def load_model(self, model3_path):
        """Load model, motion, expression and physics resources asynchronously."""

    @abstractmethod
    def parameter_ranges(self):
        """Return {parameter_id: (minimum, maximum, default)} from Cubism."""

    @abstractmethod
    def play_motion(self, group, index=0, fade_seconds=0.35):
        """Complete motions through a callback so the state machine can restore."""

    @abstractmethod
    def set_parameters(self, values):
        """Apply final mixed values once per frame, honoring Cubism ranges."""

    @abstractmethod
    def hit_test(self, x, y):
        """Return model-defined hit area name at device-independent coordinates."""

    @abstractmethod
    def set_frame_rate(self, fps):
        """Support 0 when hidden, low idle rate and bounded active rate."""

    @abstractmethod
    def close(self):
        """Cancel pending loads and release textures, motions, model and WebGL."""


def target_fps(visible, interacting):
    return 0 if not visible else 60 if interacting else 24
