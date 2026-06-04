"""HTML layout engine for picture book rendering."""

import html
import os
from typing import Any


# ---------------------------------------------------------------------------
# Layout types
# ---------------------------------------------------------------------------

LAYOUTS = [
    "full_image_text_bottom",
    "left_image_right_text",
    "full_spread",
    "text_over_image",
]


def _select_layout(text: str, page_index: int, total_pages: int) -> str:
    """Auto-select a layout based on text length and position in the book."""
    word_count = len(text.split())

    # First and last pages get the full-spread treatment
    if page_index == 0 or page_index == total_pages - 1:
        return "full_spread"

    if word_count <= 15:
        return "full_image_text_bottom"
    elif word_count <= 40:
        return "text_over_image"
    else:
        return "left_image_right_text"


def _escape(text: str) -> str:
    return html.escape(text)


def _render_page_html(page: dict, page_num: int, total_content_pages: int) -> str:
    """Render a single content page as an HTML section."""
    text = page.get("text", "")
    image_path = page.get("image_path", "")
    image_prompt = page.get("image_prompt", "")
    layout = _select_layout(text, page_num - 1, total_content_pages)

    # Use image or a placeholder gradient
    if image_path and os.path.exists(image_path):
        bg_style = f"background-image: url('{image_path}'); background-size: cover; background-position: center;"
    else:
        # Soft pastel gradient placeholder
        gradients = [
            "linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%)",
            "linear-gradient(135deg, #a1c4fd 0%, #c2e9fb 100%)",
            "linear-gradient(135deg, #d4fc79 0%, #96e6a1 100%)",
            "linear-gradient(135deg, #fbc2eb 0%, #a6c1ee 100%)",
            "linear-gradient(135deg, #f6d365 0%, #fda085 100%)",
            "linear-gradient(135deg, #e0c3fc 0%, #8ec5fc 100%)",
            "linear-gradient(135deg, #ffeaa7 0%, #dfe6e9 100%)",
        ]
        grad = gradients[(page_num - 1) % len(gradients)]
        bg_style = f"background: {grad};"

    alt_text = _escape(image_prompt or text[:80])
    escaped_text = _escape(text)

    if layout == "full_image_text_bottom":
        return f"""
        <div class="page" data-page="{page_num}">
            <div class="page-inner layout-full-bottom" style="{bg_style}">
                <div class="page-number">{page_num}</div>
                <div class="text-bottom">
                    <p>{escaped_text}</p>
                </div>
            </div>
        </div>"""

    elif layout == "left_image_right_text":
        return f"""
        <div class="page" data-page="{page_num}">
            <div class="page-inner layout-split">
                <div class="split-image" style="{bg_style}" role="img" aria-label="{alt_text}"></div>
                <div class="split-text">
                    <p>{escaped_text}</p>
                    <div class="page-number">{page_num}</div>
                </div>
            </div>
        </div>"""

    elif layout == "full_spread":
        return f"""
        <div class="page" data-page="{page_num}">
            <div class="page-inner layout-spread" style="{bg_style}">
                <div class="spread-text">
                    <p>{escaped_text}</p>
                </div>
                <div class="page-number">{page_num}</div>
            </div>
        </div>"""

    else:  # text_over_image
        return f"""
        <div class="page" data-page="{page_num}">
            <div class="page-inner layout-overlay" style="{bg_style}">
                <div class="overlay-text">
                    <p>{escaped_text}</p>
                </div>
                <div class="page-number">{page_num}</div>
            </div>
        </div>"""


def generate_book_html(pages: list[dict], book_title: str, book_id: str) -> str:
    """Generate a complete standalone HTML page for the picture book.

    Args:
        pages: List of page dicts (``text``, ``image_path``, ``image_prompt``).
        book_title: Title displayed on the cover.
        book_id: Unique identifier for the book.

    Returns:
        A complete HTML string.
    """
    escaped_title = _escape(book_title)
    total = len(pages)

    # Build content pages
    content_pages_html = ""
    for idx, page in enumerate(pages):
        content_pages_html += _render_page_html(page, idx + 1, total)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escaped_title}</title>
    <style>
        /* ================================================================
           Picture Book Viewer — Pure CSS + Vanilla JS
           ================================================================ */

        @import url('https://fonts.googleapis.com/css2?family=Patrick+Hand&family=Bubblegum+Sans&family=Quicksand:wght@400;600;700&display=swap');

        :root {{
            --page-width: min(90vw, 700px);
            --page-height: min(90vh, 700px);
            --bg-color: #f0ebe3;
            --text-color: #3d3229;
            --accent: #e07a5f;
            --accent-light: #f2cc8f;
            --shadow: 0 8px 32px rgba(0,0,0,0.18);
            --radius: 16px;
            --font-story: 'Patrick Hand', 'Comic Sans MS', 'Chalkboard SE', cursive;
            --font-ui: 'Quicksand', 'Segoe UI', sans-serif;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            background: var(--bg-color);
            background-image:
                radial-gradient(circle at 20% 80%, rgba(224,122,95,0.08) 0%, transparent 50%),
                radial-gradient(circle at 80% 20%, rgba(129,178,210,0.08) 0%, transparent 50%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            font-family: var(--font-ui);
            color: var(--text-color);
            padding: 20px;
        }}

        /* ---- Book container ---- */
        .book-container {{
            position: relative;
            width: var(--page-width);
            height: var(--page-height);
            perspective: 1800px;
        }}

        /* ---- Pages ---- */
        .page {{
            position: absolute;
            inset: 0;
            opacity: 0;
            pointer-events: none;
            transform: rotateY(30deg) scale(0.92);
            transition: opacity 0.5s ease, transform 0.6s cubic-bezier(0.4, 0, 0.2, 1);
            transform-style: preserve-3d;
        }}

        .page.active {{
            opacity: 1;
            pointer-events: auto;
            transform: rotateY(0deg) scale(1);
            z-index: 10;
        }}

        .page.prev {{
            opacity: 0;
            transform: rotateY(-40deg) scale(0.88);
        }}

        .page.next {{
            opacity: 0;
            transform: rotateY(40deg) scale(0.88);
        }}

        .page-inner {{
            width: 100%;
            height: 100%;
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            overflow: hidden;
            position: relative;
            background-color: #fff;
        }}

        /* ---- Cover ---- */
        .cover {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: linear-gradient(145deg, #e07a5f, #c1574a);
            color: #fff;
            text-align: center;
            padding: 40px;
        }}

        .cover h1 {{
            font-family: 'Bubblegum Sans', var(--font-story);
            font-size: clamp(1.8rem, 5vw, 3.2rem);
            line-height: 1.2;
            margin-bottom: 16px;
            text-shadow: 2px 3px 6px rgba(0,0,0,0.25);
        }}

        .cover .subtitle {{
            font-size: clamp(0.9rem, 2vw, 1.2rem);
            opacity: 0.85;
        }}

        .cover-back {{
            background: linear-gradient(145deg, #81b2d2, #5a9ec4);
            color: #fff;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 40px;
        }}

        .cover-back h2 {{
            font-family: 'Bubblegum Sans', var(--font-story);
            font-size: clamp(1.5rem, 4vw, 2.4rem);
            margin-bottom: 12px;
        }}

        .cover-back p {{
            font-size: 1rem;
            opacity: 0.8;
        }}

        /* ---- Layout: full image, text at bottom ---- */
        .layout-full-bottom {{
            display: flex;
            flex-direction: column;
            background-size: cover;
            background-position: center;
        }}

        .layout-full-bottom .text-bottom {{
            margin-top: auto;
            background: linear-gradient(transparent 0%, rgba(255,255,255,0.92) 30%, rgba(255,255,255,0.97) 100%);
            padding: 28px 32px 24px;
        }}

        .layout-full-bottom .text-bottom p {{
            font-family: var(--font-story);
            font-size: clamp(1.1rem, 2.8vw, 1.6rem);
            line-height: 1.65;
            color: var(--text-color);
        }}

        /* ---- Layout: split (left image, right text) ---- */
        .layout-split {{
            display: grid;
            grid-template-columns: 1fr 1fr;
        }}

        .split-image {{
            background-size: cover;
            background-position: center;
            min-height: 100%;
        }}

        .split-text {{
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 32px 28px;
            background: #fffaf4;
            position: relative;
        }}

        .split-text p {{
            font-family: var(--font-story);
            font-size: clamp(1rem, 2.4vw, 1.45rem);
            line-height: 1.7;
            color: var(--text-color);
        }}

        /* ---- Layout: full spread ---- */
        .layout-spread {{
            display: flex;
            align-items: center;
            justify-content: center;
            background-size: cover;
            background-position: center;
        }}

        .spread-text {{
            background: rgba(255,255,255,0.88);
            backdrop-filter: blur(6px);
            border-radius: 12px;
            padding: 36px 40px;
            max-width: 80%;
            text-align: center;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        }}

        .spread-text p {{
            font-family: var(--font-story);
            font-size: clamp(1.2rem, 3vw, 1.8rem);
            line-height: 1.6;
            color: var(--text-color);
        }}

        /* ---- Layout: text over image ---- */
        .layout-overlay {{
            display: flex;
            align-items: flex-end;
            background-size: cover;
            background-position: center;
        }}

        .overlay-text {{
            width: 100%;
            background: linear-gradient(transparent, rgba(255,255,255,0.93) 35%);
            padding: 60px 32px 28px;
        }}

        .overlay-text p {{
            font-family: var(--font-story);
            font-size: clamp(1.05rem, 2.6vw, 1.5rem);
            line-height: 1.65;
            color: var(--text-color);
        }}

        /* ---- Page number ---- */
        .page-number {{
            position: absolute;
            bottom: 10px;
            right: 18px;
            font-size: 0.8rem;
            color: rgba(0,0,0,0.3);
            font-family: var(--font-ui);
        }}

        /* ---- Controls ---- */
        .controls {{
            display: flex;
            align-items: center;
            gap: 20px;
            margin-top: 28px;
        }}

        .btn {{
            border: none;
            background: var(--accent);
            color: #fff;
            font-family: var(--font-ui);
            font-weight: 700;
            font-size: 1rem;
            padding: 12px 28px;
            border-radius: 50px;
            cursor: pointer;
            transition: background 0.2s, transform 0.15s;
            box-shadow: 0 3px 12px rgba(224,122,95,0.3);
        }}

        .btn:hover {{
            background: #c1574a;
            transform: translateY(-1px);
        }}

        .btn:active {{
            transform: translateY(1px);
        }}

        .btn:disabled {{
            background: #ccc;
            cursor: not-allowed;
            box-shadow: none;
            transform: none;
        }}

        .page-indicator {{
            font-size: 0.95rem;
            font-weight: 600;
            color: rgba(0,0,0,0.45);
            min-width: 80px;
            text-align: center;
        }}

        /* ---- Header ---- */
        .header {{
            margin-bottom: 20px;
            text-align: center;
        }}

        .header h1 {{
            font-family: 'Bubblegum Sans', var(--font-story);
            font-size: clamp(1.2rem, 3vw, 1.8rem);
            color: var(--accent);
        }}

        /* ---- Keyboard hint ---- */
        .hint {{
            margin-top: 12px;
            font-size: 0.8rem;
            color: rgba(0,0,0,0.35);
        }}

        /* ---- Responsive ---- */
        @media (max-width: 600px) {{
            :root {{
                --page-width: 95vw;
                --page-height: 80vh;
            }}

            .layout-split {{
                grid-template-columns: 1fr;
                grid-template-rows: 1fr 1fr;
            }}

            .controls {{
                gap: 12px;
            }}

            .btn {{
                padding: 10px 20px;
                font-size: 0.9rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{escaped_title}</h1>
    </div>

    <div class="book-container" id="book">
        <!-- Cover -->
        <div class="page active" data-page="0">
            <div class="page-inner cover">
                <h1>{escaped_title}</h1>
                <p class="subtitle">A Picture Book</p>
            </div>
        </div>

        <!-- Content pages -->
        {content_pages_html}

        <!-- Back cover -->
        <div class="page" data-page="{total + 1}">
            <div class="page-inner cover-back">
                <h2>The End</h2>
                <p>Thank you for reading!</p>
            </div>
        </div>
    </div>

    <div class="controls">
        <button class="btn" id="prevBtn" disabled>&#9664; Back</button>
        <span class="page-indicator" id="pageIndicator">Cover</span>
        <button class="btn" id="nextBtn">Next &#9654;</button>
    </div>

    <p class="hint">Use arrow keys or swipe to turn pages</p>

    <script>
        (function() {{
            const pages = document.querySelectorAll('.page');
            const totalPages = pages.length;
            let current = 0;

            const prevBtn = document.getElementById('prevBtn');
            const nextBtn = document.getElementById('nextBtn');
            const indicator = document.getElementById('pageIndicator');

            function update() {{
                pages.forEach((p, i) => {{
                    p.classList.remove('active', 'prev', 'next');
                    if (i === current) p.classList.add('active');
                    else if (i === current - 1) p.classList.add('prev');
                    else if (i === current + 1) p.classList.add('next');
                }});

                prevBtn.disabled = current === 0;
                nextBtn.disabled = current === totalPages - 1;

                if (current === 0) indicator.textContent = 'Cover';
                else if (current === totalPages - 1) indicator.textContent = 'The End';
                else indicator.textContent = 'Page ' + current + ' / ' + (totalPages - 2);
            }}

            prevBtn.addEventListener('click', () => {{ if (current > 0) {{ current--; update(); }} }});
            nextBtn.addEventListener('click', () => {{ if (current < totalPages - 1) {{ current++; update(); }} }});

            document.addEventListener('keydown', (e) => {{
                if (e.key === 'ArrowLeft' && current > 0) {{ current--; update(); }}
                if (e.key === 'ArrowRight' && current < totalPages - 1) {{ current++; update(); }}
            }});

            /* Touch / swipe support */
            let touchStartX = 0;
            const book = document.getElementById('book');
            book.addEventListener('touchstart', (e) => {{ touchStartX = e.touches[0].clientX; }}, {{passive: true}});
            book.addEventListener('touchend', (e) => {{
                const dx = e.changedTouches[0].clientX - touchStartX;
                if (Math.abs(dx) > 50) {{
                    if (dx < 0 && current < totalPages - 1) {{ current++; update(); }}
                    else if (dx > 0 && current > 0) {{ current--; update(); }}
                }}
            }}, {{passive: true}});

            update();
        }})();
    </script>
</body>
</html>"""
