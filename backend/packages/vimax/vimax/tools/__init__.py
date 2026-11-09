# rendering abstraction (concrete generators are supplied by the host app via
# RenderBackend class_path reflection — see §5.3 provider bridge). ViMax's own
# yunwu/google/openrouter generators are NOT vendored.
from .protocols import ImageGenerator, VideoGenerator
from .render_backend import RenderBackend

__all__ = ["ImageGenerator", "VideoGenerator", "RenderBackend"]
