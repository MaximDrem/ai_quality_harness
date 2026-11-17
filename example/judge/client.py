"""Клиент судьи-LLM поверх OpenAI-совместимого vLLM endpoint.

Конфиг читается из env ПРИ ИМПОРТЕ модуля — поэтому импортировать его
можно только после загрузки `.env` (см. `example.config.load_dotenv` и
ленивый импорт в `example.evaluate`).

Запросы детерминированы: temperature из env (по умолчанию 0) и
фиксированный seed=42. SYSTEM_PROMPT, сборщики промптов и JudgeResult
выбираются через get_llm_profile(scenario).
"""
from __future__ import annotations

import base64
import functools
import io
import os
import warnings
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from ..checks.llm import get_llm_profile

# with_structured_output шумит «Pydantic serializer warnings» на каждый вызов,
# хотя результат корректный. Подавляем.
warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings",
)

VLLM_BASE_URL = os.getenv(
    "VLLM_BASE_URL",
    "http://tplss-sanlp00060.sigma.sbrf.ru:8080/v1",
)

VLLM_API_KEY = os.getenv(
    "VLLM_API_KEY",
    "EMPTY",
)

MODEL_NAME = os.getenv(
    "VLLM_MODEL",
    "qwen3.8-27b",
)

TEMPERATURE = float(
    os.getenv("JUDGE_TEMPERATURE", "0.0")
)

MAX_TOKENS = int(
    # 1024 не хватает: observations по длинной колоде урезаются и
    # structured output не парсится.
    os.getenv("JUDGE_MAX_TOKENS", "4096")
)

# Максимум одновременных запросов к vLLM.
JUDGE_MAX_CONCURRENCY = int(
    os.getenv("JUDGE_MAX_CONCURRENCY", "4")
)
# Максимальная сторона изображения для судьи (LANCZOS).
JUDGE_IMG_MAX_SIDE = int(
    os.getenv("JUDGE_IMG_MAX_SIDE", "1920")
)

# Качество JPEG для отправки судье.
JUDGE_JPEG_QUALITY = int(
    os.getenv("JUDGE_JPEG_QUALITY", "90")
)


def _make_base_llm(max_tokens: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=MODEL_NAME,
        base_url=VLLM_BASE_URL,
        api_key=VLLM_API_KEY,
        temperature=TEMPERATURE,
        max_tokens=max_tokens,
        top_p=0.95,
        presence_penalty=0.0,   # дефолт OpenAI
        extra_body={
            "top_k": 20,
            "min_p": 0.0,
            "repetition_penalty": 1.0,
            # Фиксированный seed — одинаковые входы дают одинаковый вердикт.
            "seed": 42,
            "chat_template_kwargs": {"enable_thinking": False}
        }
    )


_base_llm = _make_base_llm(MAX_TOKENS)

@functools.lru_cache(maxsize=None)
def _structured_llm(scenario: str):
    profile = get_llm_profile(scenario)
    return _base_llm.with_structured_output(
        profile.JudgeResult,
        method="json_schema",
    )


async def _ainvoke_judge(messages, scenario: str):
    """Вызов судьи; при усечённом ответе повторяем с лимитом ×4."""
    try:
        return await _structured_llm(scenario).ainvoke(messages)
    except Exception as e:
        if "length limit" not in str(e):
            raise
        profile = get_llm_profile(scenario)
        llm_retry = _make_base_llm(MAX_TOKENS * 4).with_structured_output(
            profile.JudgeResult,
            method="json_schema",
        )
        return await llm_retry.ainvoke(messages)


def image_to_data_uri(
    image_path: str | Path,
) -> str:
    """Ужать изображение (LANCZOS до JUDGE_IMG_MAX_SIDE) и вернуть data-URI JPEG."""
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Image not found: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Image path is not a file: {path}"
        )

    img = Image.open(path).convert("RGB")

    side = max(img.size)
    if side > JUDGE_IMG_MAX_SIDE:
        k = JUDGE_IMG_MAX_SIDE / side
        img = img.resize(
            (round(img.width * k), round(img.height * k)),
            Image.LANCZOS,
        )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JUDGE_JPEG_QUALITY)

    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


async def judge_text(
    agent_text: str,
    user_instruction: Optional[str],
    check_instruction: str,
    scenario: str = "presentation",
):
    """Оценить текст агента по одной проверке."""
    if not agent_text.strip():
        raise ValueError("agent_text cannot be empty")
    profile = get_llm_profile(scenario)
    prompt = profile.build_text_prompt(
        agent_text=agent_text,
        user_instruction=user_instruction,
        check_instruction=check_instruction,
    )
    return await _ainvoke_judge(
        [
            ("system", profile.SYSTEM_PROMPT),
            ("human", prompt),
        ],
        scenario,
    )


async def judge_images(
    image_paths: Sequence[str | Path],
    user_instruction: Optional[str],
    check_instruction: str,
    first_slide: int = 1,
    scenario: str = "presentation",
):
    """Проверить картинки. first_slide — номер первого изображения (для
    батчей, чтобы подписи «=== СЛАЙД N ===» были сквозными).
    """
    if not image_paths:
        raise ValueError("image_paths cannot be empty")

    content: list[dict] = []
    profile = get_llm_profile(scenario)
    prompt = profile.build_vision_prompt(
        user_instruction=user_instruction,
        check_instruction=check_instruction,
        number_of_images=len(image_paths),
    )

    content.append(
        {
            "type": "text",
            "text": prompt,
        }
    )

    for i, image_path in enumerate(image_paths, start=first_slide):
        content.append({"type": "text", "text": f"=== СЛАЙД {i} ==="})
        data_uri = image_to_data_uri(image_path)
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": data_uri,
                },
            }
        )

    message = HumanMessage(content=content)

    return await _ainvoke_judge(
        [
            ("system", profile.SYSTEM_PROMPT),
            message,
        ],
        scenario,
    )