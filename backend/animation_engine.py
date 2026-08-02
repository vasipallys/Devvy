import asyncio
import json
import logging
import re
from uuid import uuid4

from backend.config import Settings

logger = logging.getLogger(__name__)


class AnimationEngine:
    """Renders a constrained explanatory Manim scene in a subprocess."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.work_dir = settings.app_data_dir / "manim"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(1)

    @staticmethod
    def _summary_lines(text: str) -> list[str]:
        plain = re.sub(r"[`#*_]", "", text)
        sentences = re.split(r"(?<=[.!?])\s+|\n+", plain)
        return [line.strip()[:90] for line in sentences if line.strip()][:5] or ["Visual explanation"]

    def _script(self, title: str, explanation: str) -> str:
        lines = self._summary_lines(explanation)
        return f'''from manim import *

class AgentExplanation(Scene):
    def construct(self):
        self.camera.background_color = "#0b0d0e"
        title = Text({json.dumps(title[:60])}, font_size=34, color="#b7f397")
        title.to_edge(UP)
        self.play(Write(title))
        items = VGroup()
        for content in {json.dumps(lines)}:
            item = Text("• " + content, font_size=22, color=WHITE)
            items.add(item)
        items.arrange(DOWN, aligned_edge=LEFT, buff=0.45).next_to(title, DOWN, buff=0.7)
        if items.width > 12:
            items.scale_to_fit_width(12)
        for item in items:
            self.play(FadeIn(item, shift=RIGHT * 0.25), run_time=0.45)
        box = SurroundingRectangle(items, color="#6f9f5a", corner_radius=0.15, buff=0.3)
        self.play(Create(box), run_time=0.7)
        self.wait(2)
'''

    async def render(self, title: str, explanation: str) -> str:
        scene_id = uuid4().hex
        script = self.work_dir / f"scene-{scene_id}.py"
        script.write_text(self._script(title, explanation), encoding="utf-8")
        output_name = f"explanation-{scene_id}.mp4"
        command = [
            self.settings.manim_executable,
            "-ql",
            "--disable_caching",
            "--media_dir",
            str(self.work_dir),
            "-o",
            output_name,
            str(script),
            "AgentExplanation",
        ]
        async with self._semaphore:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "Manim is not installed. Run pip install -e \".[visual]\" and install FFmpeg."
                ) from exc
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
            if process.returncode != 0:
                raise RuntimeError(
                    "Manim rendering failed: " + stderr.decode(errors="replace")[-800:]
                )
        matches = list(self.work_dir.rglob(output_name))
        if not matches:
            raise RuntimeError("Manim completed without producing a video")
        destination = self.settings.generated_dir / output_name
        matches[0].replace(destination)
        script.unlink(missing_ok=True)
        return f"/generated/{output_name}"
