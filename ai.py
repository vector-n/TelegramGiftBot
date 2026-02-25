"""
ai.py — All Groq AI calls using the native async client (AsyncGroq).

No blocking calls. Includes exponential-backoff retry for rate limits.
"""

import asyncio
import logging
from typing import Optional

from groq import AsyncGroq
import config

logger = logging.getLogger(__name__)
_groq = AsyncGroq(api_key=config.GROQ_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_REWRITE_SYSTEM = """\
أنت محرر محتوى محترف لقناة تيليغرام عربية متخصصة في أخبار هدايا تيليغرام الرقمية.

أسلوبك: صحفي، موجز، جذاب، واضح للقارئ العربي على الهاتف.

عند معالجة أي نص:
١. ابدأ بعنوان قصير ومثير مع إيموجي مناسب.
٢. اكتب بالعربية الفصحى السهلة القابلة للقراءة السريعة.
٣. احتفظ بكل المعلومات الأصلية: أرقام، تواريخ، أسماء، روابط، معرّفات (@username).
٤. لا تخترع أي معلومة غير موجودة في النص الأصلي.
٥. استخدم الإيموجي باعتدال لإضفاء الحيوية — لا تُبالغ.
٦. اختم المنشور بسطر فارغ ثم: #هدايا_تيليغرام
٧. الناتج: نص المنشور فقط — بدون شرح أو مقدمة أو ملاحظات.
"""

_SUMMARY_SYSTEM = """\
أنت محرر محتوى متخصص في كتابة الملخصات اليومية لقناة هدايا تيليغرام.

مهمتك: صياغة ملخص يومي احترافي، منظم، وجذاب لأبرز ما نشرناه اليوم.

اجعله:
- مقدمة قصيرة تصف اليوم بجملة.
- قائمة مرقمة بأبرز النقاط (5 نقاط كحد أقصى).
- خاتمة دافئة تدعو للمتابعة.
اختم بـ: #ملخص_اليوم #هدايا_تيليغرام

الناتج: النص فقط.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CORE CALLER WITH RETRY
# ─────────────────────────────────────────────────────────────────────────────

async def _call(
    messages: list[dict],
    max_tokens: int = 1500,
    temperature: float = 0.7,
) -> str:
    """
    Call Groq with up to 4 attempts, backing off exponentially on rate limits.
    Raises on persistent failure so callers can handle gracefully.
    """
    last_exc: Exception = RuntimeError("Unknown error")

    for attempt in range(4):
        try:
            resp = await _groq.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()

        except Exception as e:
            last_exc = e
            err_str = str(e).lower()

            if any(kw in err_str for kw in ("rate_limit", "429", "too many")):
                wait = 5 * (2 ** attempt)   # 5 → 10 → 20 → 40 seconds
                logger.warning(
                    f"⏳ Groq rate limit hit. Waiting {wait}s... (attempt {attempt + 1}/4)"
                )
                await asyncio.sleep(wait)
            else:
                logger.error(f"❌ Groq API error: {e}")
                raise

    raise last_exc


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def translate_and_rewrite(
    text: str,
    has_media: bool = False,
) -> Optional[str]:
    """
    Translate + professionally rewrite source text into Arabic.
    If there's no text but there is media, generates a short caption.
    Returns None on failure.
    """
    if not text.strip():
        if has_media:
            return await caption_for_media()
        return None

    try:
        result = await _call(
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "حوّل المنشور التالي إلى منشور عربي احترافي وجذاب:\n\n"
                        f"---\n{text}\n---\n\n"
                        "الناتج: نص المنشور العربي فقط."
                    ),
                },
            ],
            max_tokens=1500,
        )
        logger.info(f"✅ AI: {len(text)}ch → {len(result)}ch (Arabic)")
        return result

    except Exception as e:
        logger.error(f"❌ translate_and_rewrite failed: {e}")
        return None


async def caption_for_media() -> str:
    """Generate a short Arabic caption for a media-only post (no source text)."""
    try:
        return await _call(
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "اكتب تعليقاً قصيراً ومثيراً لصورة أو فيديو جديد "
                        "مرتبط بهدايا تيليغرام. أخرج النص فقط."
                    ),
                },
            ],
            max_tokens=300,
        )
    except Exception:
        return "🎁 محتوى جديد في عالم هدايا تيليغرام!\n\n#هدايا_تيليغرام"


async def daily_summary(posts: list[str]) -> Optional[str]:
    """Generate a daily digest from a list of today's posted texts."""
    if not posts:
        return None

    # Limit input to last 8 posts to stay within context
    sample = "\n\n---\n\n".join(posts[-8:])

    try:
        return await _call(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {
                    "role": "user",
                    "content": f"اكتب الملخص اليومي بناءً على هذه المنشورات:\n\n{sample}",
                },
            ],
            max_tokens=1000,
        )
    except Exception as e:
        logger.error(f"❌ daily_summary failed: {e}")
        return None
