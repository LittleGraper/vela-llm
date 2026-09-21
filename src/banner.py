"""Static VELA wordmarks, adapted from the FIGlet isometric fonts.

Original fonts by Lennert Stock; FIGlet conversion by Kent Nassen (1994).
"""

from __future__ import annotations

import random
import re

# All four letters share an 11-line height and two-column spacing.
# Keep the approved longer L stems and straight V bottom edges.
WORDMARKS: tuple[str, ...] = (
    r"""
        ___          ___            ___        ___
       /\__\        /\  \          /\__\      /\  \
      /:/  /       /::\  \        /:/  /     /::\  \
     /:/  /       /:/\:\  \      /:/  /     /:/\:\  \
    /:/  /       /::\_\:\  \    /:/  /     /::\_\:\  \
   /:/  /       /:/\:\ \:\__\  /:/__/     /:/\:\ \:\__\
  /:/__/  ___   \:\_\:\ \/__/  \:\  \     \/__\:\/:/  /
  |:|  | /\__\   \:\ \:\__\     \:\  \         \::/  /
  |:|  |/:/  /    \:\ \/__/      \:\  \        /:/  /
  |:|__/:/  /      \:\__\         \:\__\      /:/  /
   \____/__/        \/__/          \/__/      \/__/
""".strip("\n"),
    r"""
     ___             ___        ___                    ___
    /\  \           /\__\      /\  \                  /\  \
    \:\  \         /:/ _/_     \:\  \                /::\  \
     \:\  \       /:/ /\__\     \:\  \              /:/\:\  \
      \:\  \     /:/ /:/ _/_     \:\  \     ___    /:/ /::\  \
       \:\  \   /:/_/:/ /\__\     \:\  \   /\__\  /:/_/:/\:\__\
   ___  \:\__\  \:\/:/ /:/  /      \:\  \ /:/  /  \:\/:/  \/__/
  /\  \ |:|  |   \::/_/:/  /        \:\  /:/  /    \::/__/
  \:\  \|:|  |    \:\/:/  /          \:\/:/  /      \:\  \
   \:\__|:|__|     \::/  /            \::/  /        \:\__\
    \____/__/       \/__/              \/__/          \/__/
""".strip("\n"),
    r"""
     ___             ___        ___                    ___
    /__/\           /  /\      /__/\                  /  /\
    \  \:\         /  /:/_     \  \:\                /  /::\
     \  \:\       /  /:/ /\     \  \:\              /  /:/\:\
      \  \:\     /  /:/ /:/_     \  \:\     ___    /  /:/_/::\
       \  \:\   /__/:/ /:/ /\     \  \:\   /  /\  /__/:/ /:/\:\
   ___  \__\:\  \  \:\/:/ /:/      \  \:\ /  /:/  \  \:\/:/__\/
  /__/\ |  |:|   \  \::/ /:/        \  \:\  /:/    \  \::/
  \  \:\|  |:|    \  \:\/:/          \  \:\/:/      \  \:\
   \  \:\__|:|     \  \::/            \  \::/        \  \:\
    \__\____/       \__\/              \__\/          \__\/
""".strip("\n"),
    r"""
        ___          ___            ___        ___
       /  /\        /  /\          /  /\      /  /\
      /  /:/       /  /::\        /  /:/     /  /::\
     /  /:/       /  /:/\:\      /  /:/     /  /:/\:\
    /  /:/       /  /::\ \:\    /  /:/     /  /::\ \:\
   /  /:/       /__/:/\:\ \:\  /__/:/     /__/:/\:\_\:\
  /__/:/  ___   \  \:\ \:\_\/  \  \:\     \__\/  \:\/:/
  |  |:| /  /\   \  \:\ \:\     \  \:\         \__\::/
  |  |:|/  /:/    \  \:\_\/      \  \:\        /  /:/
  |__|:|__/:/      \  \:\         \  \:\      /__/:/
   \__\____/        \__\/          \__\/      \__\/
""".strip("\n"),
)


def render_startup_banner(*, color: bool = False, width: int = 80) -> str:
    """Pick one wordmark per launch, falling back to text in narrow terminals."""
    wordmark = random.choice(WORDMARKS)
    if max(map(len, wordmark.splitlines())) > width:
        return "\033[38;2;103;232;249mVELA\033[0m" if color else "VELA"
    if not color:
        return wordmark

    def colorize(match: re.Match[str]) -> str:
        text = match.group()
        if text[0] in ":.":
            rgb = "56;126;156"
        elif text[0] == "_":
            rgb = "160;243;255"
        else:
            rgb = "103;232;249"
        return f"\033[38;2;{rgb}m{text}\033[0m"

    return re.sub(r"[:.]+|_+|[/\\|]+", colorize, wordmark)
