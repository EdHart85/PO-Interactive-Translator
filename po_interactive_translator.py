#!/usr/bin/env python3
"""
PO Interactive Translator
=========================
An interactive, terminal-based CAT (Computer-Assisted Translation) tool to
translate Gettext (.po) files using an online machine-translation engine
(Google Translate via the `deep-translator` library).

It was originally built to help translate the Siril astrophotography software
from English to Portuguese, so the DEFAULT settings are tuned for that use
case. However, every Siril-specific behaviour is configurable through
command-line options, which makes the tool generic enough for any software
project and any language pair.

Key features
------------
- Protects software command names from being translated (configurable marker;
  defaults to Siril's ``Commands.rst:-1`` occurrence tag).
- Protects inline code blocks (``code``) and bold text (**bold**).
- Restores the correct casing for protected brand / software names loaded from
  an external file.
- Interactive terminal interface with optional ANSI colours.
- Handles plural forms, fuzzy entries and the PO header safely.
- Automatic, resumable progress saving.
- Optional non-interactive batch mode (``--auto``).
- Automatic retry with exponential back-off and chunking for long strings.

Author : Your Name (https://github.com/your-handle)
License: MIT
"""

from __future__ import annotations

import argparse
import os
import textwrap
import re
import sys
import time
from typing import Iterable, List, Optional, Sequence, Set

# --------------------------------------------------------------------------- #
# Defaults (tuned for the Siril use case, but fully overridable via the CLI)   #
# --------------------------------------------------------------------------- #
DEFAULT_SOURCE_LANG = "en"
DEFAULT_TARGET_LANG = "pt"
DEFAULT_BRANDS_FILE = "protected_brands.txt"
# Siril marks command definitions with this occurrence reference.
DEFAULT_COMMANDS_MARKER = "Commands.rst:-1"
DEFAULT_BRANDS: List[str] = [
    "Siril", "N.I.N.A", "GraXpert", "Starnet++", "Astro-Team",
    "Poedit", "Weblate", "GitLab", "GitHub", "Python", "APT", "Ekos",
]

AUTOSAVE_EVERY = 10          # save progress every N entries
MAX_CHUNK_CHARS = 4500       # stay below the ~5000 char engine limit
MAX_RETRIES = 3              # translation retry attempts
RETRY_BACKOFF = 1.5          # seconds, multiplied each retry

# --------------------------------------------------------------------------- #
# Colours                                                                      #
# --------------------------------------------------------------------------- #
class Colors:
    """ANSI colour codes. Replaced by empty strings when colour is disabled."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"

    @classmethod
    def disable(cls) -> None:
        for name in ("RESET", "BOLD", "GREEN", "YELLOW", "BLUE",
                     "CYAN", "RED", "MAGENTA"):
            setattr(cls, name, "")


C = Colors


# --------------------------------------------------------------------------- #
# Dependency check (advisory only - no automatic install)                      #
# --------------------------------------------------------------------------- #
def ensure_dependencies() -> None:
    """Verify required third-party libraries are present and give clear help."""
    missing = []
    try:
        import polib  # noqa: F401
    except ImportError:
        missing.append("polib")
    try:
        from deep_translator import GoogleTranslator  # noqa: F401
    except ImportError:
        missing.append("deep-translator")

    if missing:
        sys.stderr.write(
            f"{C.RED}Missing required dependencies: {', '.join(missing)}.{C.RESET}\n"
            f"Install them with:\n\n"
            f"    pip install -r requirements.txt\n"
            f"  or\n"
            f"    pip install {' '.join(missing)}\n\n"
        )
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Terminal helpers                                                             #
# --------------------------------------------------------------------------- #
def clear_screen() -> None:
    """Clear the terminal screen (skipped when output is not a TTY)."""
    if not sys.stdout.isatty():
        return
    os.system("cls" if os.name == "nt" else "clear")


def print_header(current: int, total: int, target_lang: str) -> None:
    """Print the interactive header with quick-command help."""
    print(f"{C.CYAN}{C.BOLD}" + "=" * 80)
    print(f" PO INTERACTIVE TRANSLATOR | Progress: {current}/{total} "
          f"| Target: [{target_lang.upper()}]")
    print("=" * 80 + f"{C.RESET}")
    print(f"{C.YELLOW}{C.BOLD}Quick Commands:{C.RESET}")
    print(f"  - {C.GREEN}[ENTER]{C.RESET}            : Accept the suggested translation")
    print(f"  - {C.RED}n{C.RESET} / {C.RED}no{C.RESET}             : Reject (leave empty for manual review)")
    print(f"  - {C.BLUE}s{C.RESET} / {C.BLUE}skip{C.RESET}           : Skip without changing this entry")
    print(f"  - {C.BLUE}q{C.RESET} / {C.BLUE}quit{C.RESET}           : Save current progress and exit")
    print(f"  - {C.MAGENTA}[Any other text]{C.RESET}  : Save the typed text as a custom translation")
    print(f"{C.CYAN}" + "-" * 80 + f"{C.RESET}\n")


# --------------------------------------------------------------------------- #
# Configuration loading                                                        #
# --------------------------------------------------------------------------- #
def load_protected_brands(filename: str) -> Set[str]:
    """
    Load protected brand / software names from an external file.

    If the file does not exist it is created with sensible defaults.
    Blank lines and lines starting with ``#`` are ignored.
    """
    if not os.path.exists(filename):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("# Add brands, software, or terms that should NOT be "
                        "translated (one per line)\n")
                f.write("\n".join(DEFAULT_BRANDS) + "\n")
            print(f"{C.GREEN}Created default configuration file: "
                  f"'{filename}'{C.RESET}")
        except OSError as exc:
            print(f"{C.RED}Warning: could not create '{filename}': {exc}. "
                  f"Using built-in defaults.{C.RESET}")
        return set(DEFAULT_BRANDS)

    try:
        brands: Set[str] = set()
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    brands.add(line)
        return brands or set(DEFAULT_BRANDS)
    except OSError as exc:
        print(f"{C.RED}Error reading '{filename}': {exc}. "
              f"Using built-in defaults.{C.RESET}")
        return set(DEFAULT_BRANDS)


# --------------------------------------------------------------------------- #
# Translation post-processing                                                  #
# --------------------------------------------------------------------------- #
def _should_protect(token: str, commands_lower: Set[str]) -> bool:
    """Heuristic deciding whether an inline token must keep its original form."""
    clean = token.strip()
    return (
        clean.lower() in commands_lower
        or clean.startswith("-")          # CLI argument, e.g. -out=
        or bool(re.fullmatch(r"[A-Za-z0-9_./+-]{1,4}", clean))  # short code term
    )


def protect_inline_elements(original: str, translated: str,
                            commands: Iterable[str],
                            brands: Iterable[str]) -> str:
    """
    Restore technical content in the translated string.

    * inline code  ``code``   -> original kept when it is a command/argument
    * bold text    **bold**   -> original kept when it is a command/argument
    * brand names              -> correct casing restored (regex-safe)
    """
    commands_lower = {c.lower() for c in commands}

    # marker -> non-greedy pattern capturing the content between the markers
    marker_patterns = {
        "``": r"``(.+?)``",
        "**": r"\*\*(.+?)\*\*",
    }
    for marker, pattern in marker_patterns.items():
        orig_tokens = re.findall(pattern, original)
        trans_tokens = re.findall(pattern, translated)
        # Only act when the structure was preserved by the engine.
        if orig_tokens and len(orig_tokens) == len(trans_tokens):
            for orig_tok, trans_tok in zip(orig_tokens, trans_tokens):
                if _should_protect(orig_tok, commands_lower):
                    translated = translated.replace(
                        f"{marker}{trans_tok}{marker}",
                        f"{marker}{orig_tok}{marker}",
                        1,
                    )

    # Restore brand casing - escape brand so '+', '.', etc. are literal.
    for brand in brands:
        pattern = rf"(?<!\w){re.escape(brand)}(?!\w)"
        translated = re.sub(pattern, brand, translated, flags=re.IGNORECASE)

    return translated


# --------------------------------------------------------------------------- #
# Machine translation with retry + chunking                                    #
# --------------------------------------------------------------------------- #
def _chunk_text(text: str, limit: int = MAX_CHUNK_CHARS) -> List[str]:
    """Split text into chunks under the engine character limit, on whitespace."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for word in re.split(r"(\s+)", text):
        if len(current) + len(word) > limit and current:
            chunks.append(current)
            current = ""
        current += word
    if current:
        chunks.append(current)
    return chunks


def translate_text(translator, text: str) -> str:
    """Translate `text`, retrying on failure and handling long strings."""
    if not text.strip():
        return text

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            parts = _chunk_text(text)
            out = "".join(translator.translate(p) or "" for p in parts)
            return out
        except Exception as exc:  # network / rate-limit / engine errors
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise RuntimeError(f"translation failed after {MAX_RETRIES} attempts: "
                       f"{last_error}")


# --------------------------------------------------------------------------- #
# PO helpers                                                                   #
# --------------------------------------------------------------------------- #
def needs_translation(entry, include_fuzzy: bool) -> bool:
    """Return True when an entry still requires (re)translation."""
    if not entry.msgid:                      # PO header
        return False
    if entry.obsolete:                       # obsolete (#~) entries
        return False
    if include_fuzzy and "fuzzy" in entry.flags:
        return True
    if entry.msgid_plural:
        return any(not v for v in entry.msgstr_plural.values())
    return not entry.msgstr


def apply_translation(entry, translator, commands_list, brands) -> None:
    """Fill an entry's msgstr / plural forms with protected translations."""
    if entry.msgid_plural:
        singular = protect_inline_elements(
            entry.msgid, translate_text(translator, entry.msgid),
            commands_list, brands)
        plural = protect_inline_elements(
            entry.msgid_plural, translate_text(translator, entry.msgid_plural),
            commands_list, brands)
        for key in sorted(entry.msgstr_plural.keys()):
            entry.msgstr_plural[key] = singular if key == 0 else plural
    else:
        raw = translate_text(translator, entry.msgid)
        entry.msgstr = protect_inline_elements(entry.msgid, raw,
                                               commands_list, brands)
    if "fuzzy" in entry.flags:
        entry.flags.remove("fuzzy")


def entry_preview(entry) -> str:
    """Human-readable source text for display."""
    if entry.msgid_plural:
        return f"{entry.msgid}\n[plural] {entry.msgid_plural}"
    return entry.msgid


def entry_suggestion(entry) -> str:
    """Return the current msgstr (singular) for confirmation display."""
    if entry.msgid_plural:
        return entry.msgstr_plural.get(0, "")
    return entry.msgstr


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="po_interactive_translator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Interactive machine-assisted translator for Gettext (.po) files.\n"
            "Defaults are tuned for the Siril project (en -> pt, Commands.rst\n"
            "marker, astronomy brands), but everything is configurable."),
        epilog=textwrap.dedent("""\
            Examples
            --------
            # Interactive translation with Siril defaults (en -> pt):
            python po_interactive_translator.py siril.po

            # Translate another project to Spanish, no command protection:
            python po_interactive_translator.py app.po -l es --commands-marker ""

            # Fully automatic batch mode, custom brands file, French:
            python po_interactive_translator.py app.po -l fr --auto \\
                --brands-file my_brands.txt

            # Include fuzzy entries and disable colours:
            python po_interactive_translator.py siril.po --include-fuzzy --no-color
            """),
    )
    parser.add_argument("input", nargs="?",
                        help="Path to the input .po (or .po.txt) file.")
    parser.add_argument("-o", "--output",
                        help="Output file path "
                             "(default: <input>_translated.po).")
    parser.add_argument("-l", "--target-lang", default=DEFAULT_TARGET_LANG,
                        help=f"Target language code "
                             f"(default: {DEFAULT_TARGET_LANG}).")
    parser.add_argument("-s", "--source-lang", default=DEFAULT_SOURCE_LANG,
                        help=f"Source language code, or 'auto' "
                             f"(default: {DEFAULT_SOURCE_LANG}).")
    parser.add_argument("--brands-file", default=DEFAULT_BRANDS_FILE,
                        help=f"File with protected brand/term names "
                             f"(default: {DEFAULT_BRANDS_FILE}).")
    parser.add_argument("--commands-marker", default=DEFAULT_COMMANDS_MARKER,
                        help="PO occurrence reference that marks command "
                             "definitions to keep untranslated. Pass an empty "
                             f"string to disable (default: "
                             f"'{DEFAULT_COMMANDS_MARKER}').")
    parser.add_argument("--auto", action="store_true",
                        help="Non-interactive: accept every machine "
                             "translation automatically.")
    parser.add_argument("--include-fuzzy", action="store_true",
                        help="Also (re)translate entries flagged as fuzzy.")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable ANSI colours.")
    return parser


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        C.disable()
    elif os.name == "nt":
        os.system("")  # enable ANSI on legacy Windows terminals

    ensure_dependencies()
    import polib
    from deep_translator import GoogleTranslator

    # ---- input file -------------------------------------------------------- #
    input_file = args.input
    if not input_file:
        clear_screen()
        print(f"{C.CYAN}{C.BOLD}=== PO INTERACTIVE TRANSLATOR ==={C.RESET}\n")
        input_file = input(
            f"{C.YELLOW}Drag and drop your .po/.txt file here "
            f"(or type the path) and press Enter:{C.RESET}\n").strip().strip("'\"")

    if not input_file or not os.path.exists(input_file):
        print(f"\n{C.RED}{C.BOLD}Error: file '{input_file}' not found."
              f"{C.RESET}")
        return 1

    # ---- output file ------------------------------------------------------- #
    if args.output:
        output_file = args.output
    else:
        base, ext = os.path.splitext(input_file)
        if ext.lower() == ".txt" and base.lower().endswith(".po"):
            output_file = base[:-3] + "_translated.po"
        else:
            output_file = base + "_translated.po"

    target_lang = args.target_lang.strip().lower() or DEFAULT_TARGET_LANG
    protected_brands = load_protected_brands(args.brands_file)

    print(f"\n{C.CYAN}Loading translation file...{C.RESET}")
    try:
        po = polib.pofile(input_file)
    except Exception as exc:
        print(f"{C.RED}{C.BOLD}Failed to parse PO file: {exc}{C.RESET}")
        return 1

    # ---- protect command entries ------------------------------------------ #
    commands_list: Set[str] = set()
    marker = args.commands_marker
    if marker:
        for entry in po:
            if any(marker in occ[0] for occ in entry.occurrences):
                commands_list.add(entry.msgid)
                if not entry.msgstr and not entry.msgid_plural:
                    entry.msgstr = entry.msgid  # command == its own translation

    # ---- collect pending entries ------------------------------------------ #
    pending = [e for e in po if needs_translation(e, args.include_fuzzy)]
    total = len(pending)

    if total == 0:
        clear_screen()
        print(f"{C.GREEN}{C.BOLD}All strings are already translated!{C.RESET}")
        po.save(output_file)
        return 0

    translator = GoogleTranslator(source=args.source_lang, target=target_lang)

    # ---- automatic batch mode --------------------------------------------- #
    if args.auto:
        print(f"{C.CYAN}Auto-translating {total} entries to "
              f"[{target_lang.upper()}]...{C.RESET}")
        for idx, entry in enumerate(pending, 1):
            try:
                apply_translation(entry, translator, commands_list,
                                  protected_brands)
            except Exception as exc:
                print(f"{C.RED}[{idx}/{total}] error: {exc}{C.RESET}")
            if idx % AUTOSAVE_EVERY == 0:
                po.save(output_file)
                print(f"{C.BLUE}  ...{idx}/{total} saved{C.RESET}")
        po.save(output_file)
        print(f"{C.GREEN}{C.BOLD}Done. Saved to '{output_file}'.{C.RESET}")
        return 0

    # ---- interactive loop -------------------------------------------------- #
    try:
        for idx, entry in enumerate(pending):
            clear_screen()
            print_header(idx + 1, total, target_lang)

            print(f"{C.YELLOW}{C.BOLD}Original:{C.RESET}")
            print(f"{entry_preview(entry)}\n")
            print(f"{C.CYAN}" + "-" * 40 + f"{C.RESET}\n")

            try:
                apply_translation(entry, translator, commands_list,
                                  protected_brands)
                suggested = entry_suggestion(entry)
                print(f"{C.GREEN}{C.BOLD}Suggested Translation:{C.RESET}")
                print(f"{suggested}\n")
            except Exception as exc:
                suggested = ""
                entry.msgstr = ""
                print(f"{C.RED}Error fetching translation: {exc}{C.RESET}")
                print("Suggestion: [left blank]\n")

            print(f"{C.CYAN}" + "-" * 40 + f"{C.RESET}")
            user_input = input(f"{C.BOLD}Your Action: {C.RESET}").strip()
            low = user_input.lower()

            if user_input == "":
                pass  # keep suggested translation already applied
            elif low in ("n", "no", "nao", "não"):
                entry.msgstr = ""
                if entry.msgid_plural:
                    for k in entry.msgstr_plural:
                        entry.msgstr_plural[k] = ""
            elif low in ("s", "skip", "pular"):
                continue
            elif low in ("q", "quit", "sair"):
                clear_screen()
                print(f"\n{C.CYAN}Saving progress and exiting...{C.RESET}")
                po.save(output_file)
                print(f"{C.GREEN}{C.BOLD}Progress saved to "
                      f"'{output_file}'{C.RESET}")
                return 0
            else:
                entry.msgstr = user_input

            if (idx + 1) % AUTOSAVE_EVERY == 0:
                po.save(output_file)

        clear_screen()
        po.save(output_file)
        print(f"\n{C.GREEN}{C.BOLD}All strings reviewed and saved to "
              f"'{output_file}'.{C.RESET}")
        return 0

    except KeyboardInterrupt:
        clear_screen()
        print(f"\n\n{C.RED}Interrupted. Saving current progress...{C.RESET}")
        po.save(output_file)
        print(f"{C.GREEN}{C.BOLD}Progress saved to '{output_file}'.{C.RESET}")
        return 130


if __name__ == "__main__":
    sys.exit(main())
