"""Render the initial climbing-wall SVG for subsequent browser animation.

The transform group IDs form the interface used by app.js. This module only
maps display values to a schematic pose; it does not estimate physical position.
"""

from html import escape


def _pose(snapshot):
    """Map display angles and demo progress to SVG coordinates and transforms."""
    # Keep these visual scale factors aligned with the browser's animation targets.
    return (
        320 + snapshot["roll"] * 2.3,
        550 - snapshot["progress"] * 3.9,
        snapshot["roll"] * 0.4,
        1 + snapshot["pitch"] / 450,
        -snapshot["yaw"] * 0.8,
    )


def render_wall(snapshot):
    """Build a deterministic wall and an escaped, accessible initial avatar pose."""
    x, y, lean, scale, twist = _pose(snapshot)
    holds = []
    # Stagger the hold grid and vary its shapes without random per-load changes.
    for row in range(7):
        for col in range(5):
            hx = 110 + col * 104 + (18 if row % 2 else -10)
            hy = 100 + row * 82
            color = ("#568e88", "#b88457", "#737f9e")[(row + col) % 3]
            angle = ((row * 31 + col * 17) % 60) - 30
            holds.append(
                f'<path d="M-14,3 Q-17,-7 -6,-10 L8,-8 Q19,-2 12,6 L0,11 Z" '
                f'fill="{color}" stroke="#0d1c27" stroke-width="3" '
                f'transform="translate({hx} {hy}) rotate({angle})"/>'
                f'<circle cx="{hx}" cy="{hy}" r="2" fill="#172633"/>'
            )
    state = escape(snapshot["status"])
    # Position, scale, lean, and twist stay in separate groups so the browser can
    # animate each transform without replacing geometry or rebuilding the SVG.
    return (
        '<div class="bb-wall"><svg viewBox="0 0 640 730" role="img" '
        f'aria-label="Schematic climbing wall. {state}. '
        f'Demo progress {snapshot["progress"]:.0f} percent.">\n'
        '<defs><pattern id="grid" width="80" height="82" patternUnits="userSpaceOnUse">'
        '<path d="M80 0H0V82" fill="none" stroke="#243b4c" stroke-width="1"/>'
        '<circle cx="40" cy="41" r="2" fill="#355063"/></pattern>'
        '<linearGradient id="wallShade" x2="1" y2="1"><stop stop-color="#1e3444"/>'
        '<stop offset="1" stop-color="#142431"/></linearGradient></defs>\n'
        '<rect width="640" height="730" fill="url(#wallShade)"/>'
        '<rect x="55" y="58" width="530" height="602" rx="12" '
        'fill="url(#grid)" stroke="#3a5365"/>\n'
        '<text x="28" y="32" fill="#a9bccb" font-size="13" font-family="sans-serif" '
        'letter-spacing="3">BELAYBUDDY / MOVEMENT LAB</text>\n'
        '<path d="M320 670 C290 560 350 380 320 80" fill="none" stroke="#58dac3" '
        'opacity=".16" stroke-width="3" stroke-dasharray="5 9"/>\n'
        f'{"".join(holds)}\n'
        '<ellipse cx="320" cy="679" rx="100" ry="8" fill="#0b1720" opacity=".6"/>\n'
        f'<g id="avatar-position" transform="translate({x:.2f} {y:.2f})">\n'
        f'<g id="avatar-scale" transform="scale({scale:.3f})">\n'
        f'<g id="avatar-lean" transform="rotate({lean:.2f})">\n'
        '<path d="M-9 6 L-39 43 L-53 83 M9 6 L39 39 L53 74" fill="none" '
        'stroke="#91a8bc" stroke-width="14" stroke-linecap="round" '
        'stroke-linejoin="round"/>\n'
        '<path d="M-53 83 l-16 3 M53 74 l15 -4" stroke="#0b151f" '
        'stroke-width="14" stroke-linecap="round"/>\n'
        f'<g id="avatar-twist" transform="rotate({twist:.2f} 0 -35)">\n'
        '<path d="M-20 -61 L-48 -79 L-63 -112 M20 -61 L50 -47 L77 -73" fill="none" '
        'stroke="#ecc8a0" stroke-width="11" stroke-linecap="round" '
        'stroke-linejoin="round"/>\n'
        '<path d="M-22 -66 Q0 -76 22 -66 L15 -4 Q0 8 -15 -4 Z" fill="#58dac3" '
        'stroke="#a5f1e3" stroke-width="2"/>\n'
        '<path d="M-16 -13 L16 -13 M0 -14 L0 3" stroke="#23333f" stroke-width="6"/>\n'
        '<circle cx="0" cy="-94" r="19" fill="#ecc8a0"/>'
        '<path d="M-20 -98 A20 20 0 0 1 20 -98 Z" fill="#efae69"/>\n'
        '<path d="M-7 -96 L7 -96" stroke="#283744" stroke-width="3" '
        'stroke-linecap="round"/>\n'
        '</g><circle cx="0" cy="-8" r="5" fill="#e8eff5"/></g></g></g>\n'
        '<text x="28" y="710" fill="#a9bccb" font-size="13" font-family="sans-serif">'
        'VISUAL INTERPRETATION</text><text id="wall-progress" x="611" y="710" '
        'fill="#58dac3" text-anchor="end" font-size="13" font-family="sans-serif">'
        f'{snapshot["progress"]:.0f}% DEMO</text>\n'
        '</svg></div>'
    )
