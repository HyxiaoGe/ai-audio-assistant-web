"""Batch generate ~100 AI templates (10 per category) via PromptHub + LLM pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging  # noqa: E402

from app.db import async_session_factory  # noqa: E402

# Import LLM service modules to trigger @register_service decorators
from app.services.llm import configs as llm_configs  # noqa: E402, F401
from app.services.llm import openrouter  # noqa: E402, F401
from app.services.template_generator import TemplateGenerator  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Starting batch template generation...")

    async with async_session_factory() as db:
        generator = TemplateGenerator(db)
        try:
            result = await generator.batch_generate(count_per_category=10)

            # Print report
            print("\n" + "=" * 60)
            print("TEMPLATE GENERATION REPORT")
            print("=" * 60)
            for stat in result.stats:
                print(
                    f"  {stat.category:<20} "
                    f"generated={stat.generated:<4} "
                    f"passed={stat.passed_quality:<4} "
                    f"saved={stat.saved}"
                )
            print("-" * 60)
            print(f"  Total generated: {result.total_generated}")
            print(f"  Total saved:     {result.total_saved}")
            print("=" * 60)

        finally:
            await generator.close()

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
