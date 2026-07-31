"""Runtime configuration: engine device/backend/timeouts and output conventions."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


def _default_device() -> str:
    """Compute device. macOS uses the Apple GPU via MLX; other platforms default
    to CPU so the app runs anywhere -- CUDA users can override."""
    return "mps" if sys.platform == "darwin" else "cpu"


@dataclass
class ConvertOptions:
    # MinerU backend. vlm-engine runs the high-accuracy VLM locally (MLX on
    # Apple Silicon) and matches hybrid-engine output byte for byte while
    # skipping a redundant layout/OCR-det pass.
    mineru_backend: str = "vlm-engine"
    # MINERU_DEVICE_MODE: mps (macOS) / cuda / cpu, see _default_device()
    device: str = field(default_factory=_default_device)
    timeout_sec: int = 1800       # per-file recognition ceiling
    # Without api_url, mineru spins up a throwaway service and reloads the 1.2B
    # model on every call (~12s per file). Batch runs point this at a resident
    # MineruServer instead.
    api_url: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    # Digital PDFs skip the model entirely; formula-bearing files and scans fall
    # back automatically. See textlayer.probe(). Disable to always use the model.
    fast_text_layer: bool = True
    # Parallel workers for multi-page PDFs. The engine handles one request at a
    # time, so speedup comes from extra instances (~1.49x at 2 workers, sharing
    # GPU bandwidth). Each needs ~2 GB free RAM; 1 disables parallelism.
    max_workers: int = 2
    output_subdir: str = "_word"  # output dir suffix: <stem>_word beside the source
    # Export format: docx (formulas as OMML) or md (formulas kept as LaTeX).
    export_format: str = "docx"
    # Flag every formula/table for review. Off by default: recognition is
    # reliable enough that a wall of correct entries buries the few real
    # problems. Conversion failures are always flagged regardless.
    review_all_formulas: bool = False
    # ---- Cloud recognition (official mineru.net API) ----
    # A non-empty key routes recognition to the cloud: no local model needed,
    # at the cost of uploading files. Empty means local model.
    api_token: str = ""
    cloud_model: str = "vlm"      # official model_version: pipeline / vlm / MinerU-HTML
    cloud_language: str = "ch"

    @property
    def use_cloud(self) -> bool:
        return bool(self.api_token)
